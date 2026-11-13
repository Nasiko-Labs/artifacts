//! The A2A server: agent card + JSON-RPC (`SendMessage`, `SendStreamingMessage`) on one axum router.
//!
//! Stateless: every message runs one fresh turn and the task always ends `completed` (or `failed` on an
//! internal error). Nothing pauses, nothing is resumed, nothing is stored: the task store is in-memory and
//! only matters for the life of the request; earlier turns come from the platform's history slice.
//!
//! A finished turn's artifact (`result`) carries the reply text (the chunk prompts, or the merged result as a
//! fenced json block) and, for a split or a merge, a data part with the envelope; the completed status
//! message carries the same text. There is no model call, so the inbound
//! traceparent authorises nothing here; it only parents the request span.

use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::Arc;

use a2a::*;
use a2a_server::*;
use futures::stream::BoxStream;

use crate::agent;
use crate::card;
use crate::inputs;
use crate::telemetry;

const FAILED_TEXT: &str =
    "I couldn't finish this request: something went wrong while formatting. Please try again.";

pub struct ChunkerExecutor;

impl AgentExecutor for ChunkerExecutor {
    fn execute(
        &self,
        ctx: ExecutorContext,
    ) -> BoxStream<'static, Result<StreamResponse, A2AError>> {
        let task_id = ctx.task_id.clone();
        let context_id = ctx.context_id.clone();
        let incoming = ctx
            .message
            .as_ref()
            .map(|m| inputs::read_parts(&m.parts))
            .unwrap_or_default();
        let span = request_span(&ctx, &incoming.text);
        let record_span = span.clone();

        let stream = async_stream::stream! {
            yield Ok(status(&task_id, &context_id, TaskState::Working, None));
            // The turn is pure code; an internal error (or a bug) fails this task instead of the server.
            let result = match catch_unwind(AssertUnwindSafe(|| agent::turn(&incoming))) {
                Ok(Ok(result)) => result,
                Ok(Err(error)) => {
                    tracing::error!(%error, "chunker-merger turn failed");
                    yield Ok(status(&task_id, &context_id, TaskState::Failed, Some(FAILED_TEXT)));
                    return;
                }
                Err(_) => {
                    tracing::error!("chunker-merger turn panicked");
                    yield Ok(status(&task_id, &context_id, TaskState::Failed, Some(FAILED_TEXT)));
                    return;
                }
            };
            let mut parts = vec![Part::text(&result.reply)];
            parts.extend(result.envelope.clone().map(Part::data));
            yield Ok(StreamResponse::ArtifactUpdate(TaskArtifactUpdateEvent {
                task_id: task_id.clone(),
                context_id: context_id.clone(),
                artifact: Artifact {
                    artifact_id: new_artifact_id(),
                    name: Some("result".into()),
                    description: None,
                    parts,
                    metadata: None,
                    extensions: None,
                },
                append: Some(false),
                last_chunk: Some(true),
                metadata: None,
            }));
            if telemetry::capture_content() {
                let output = telemetry::genai_text_message("assistant", &result.reply).to_string();
                record_span.record("gen_ai.output.messages", output.as_str());
            }
            yield Ok(status(&task_id, &context_id, TaskState::Completed, Some(&result.reply)));
        };

        use futures::StreamExt as _;
        use tracing::Instrument as _;
        Box::pin(async_stream::stream! {
            let mut inner = std::pin::pin!(stream);
            while let Some(item) = inner.next().instrument(span.clone()).await {
                yield item;
            }
        })
    }

    fn cancel(&self, ctx: ExecutorContext) -> BoxStream<'static, Result<StreamResponse, A2AError>> {
        let (task_id, context_id) = ctx.task_info();
        Box::pin(futures::stream::once(async move {
            Ok(status(&task_id, &context_id, TaskState::Canceled, None))
        }))
    }
}

/// The GenAI `invoke_agent` span for one request, parented to the caller's trace when it sent one.
fn request_span(ctx: &ExecutorContext, user_text: &str) -> tracing::Span {
    let span = tracing::info_span!(
        "a2a.execute",
        otel.kind = "server",
        gen_ai.operation.name = "invoke_agent",
        gen_ai.agent.name = %telemetry::service_name(),
        session.id = %ctx.context_id,
        gen_ai.input.messages = tracing::field::Empty,
        gen_ai.output.messages = tracing::field::Empty,
    );
    let remote = ctx
        .service_params
        .get("traceparent")
        .and_then(|v| v.first())
        .and_then(|tp| telemetry::remote_context_from_traceparent(tp));
    if let Some(cx) = remote {
        use tracing_opentelemetry::OpenTelemetrySpanExt as _;
        span.set_parent(cx);
    }
    if telemetry::capture_content() && !user_text.is_empty() {
        let input = telemetry::genai_text_message("user", user_text).to_string();
        span.record("gen_ai.input.messages", input.as_str());
    }
    span
}

fn status(task_id: &str, context_id: &str, state: TaskState, text: Option<&str>) -> StreamResponse {
    StreamResponse::StatusUpdate(TaskStatusUpdateEvent {
        task_id: task_id.into(),
        context_id: context_id.into(),
        status: TaskStatus {
            state,
            message: text.map(|t| Message {
                message_id: new_message_id(),
                context_id: Some(context_id.into()),
                task_id: Some(task_id.into()),
                role: Role::Agent,
                parts: vec![Part::text(t)],
                metadata: None,
                extensions: None,
                reference_task_ids: None,
            }),
            timestamp: Some(chrono::Utc::now()),
        },
        metadata: None,
    })
}

pub fn agent_card(host: &str, port: u16) -> AgentCard {
    let strings = |items: &[&str]| items.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>();
    AgentCard {
        name: card::AGENT_NAME.into(),
        description: card::AGENT_DESCRIPTION.into(),
        version: card::AGENT_VERSION.into(),
        provider: Some(AgentProvider {
            organization: "Nasiko".into(),
            url: "https://nasiko.io".into(),
        }),
        capabilities: AgentCapabilities {
            streaming: Some(false),
            push_notifications: Some(false),
            extensions: None,
            extended_agent_card: None,
        },
        skills: vec![AgentSkill {
            id: card::SKILL_ID.into(),
            name: card::SKILL_NAME.into(),
            description: card::SKILL_DESCRIPTION.into(),
            tags: strings(&card::SKILL_TAGS),
            examples: Some(strings(&card::SKILL_EXAMPLES)),
            input_modes: Some(strings(&card::INPUT_MODES)),
            output_modes: Some(strings(&card::OUTPUT_MODES)),
            security_requirements: None,
        }],
        default_input_modes: strings(&card::INPUT_MODES),
        default_output_modes: strings(&card::OUTPUT_MODES),
        supported_interfaces: vec![AgentInterface::new(
            format!("http://{host}:{port}/"),
            TRANSPORT_PROTOCOL_JSONRPC,
        )],
        security_schemes: None,
        security_requirements: None,
        documentation_url: None,
        icon_url: None,
        signatures: None,
    }
}

/// The whole HTTP app: agent card routes + JSON-RPC at `/`, backed by a request-lifetime task store.
pub fn app(host: &str, port: u16) -> axum::Router {
    let handler = Arc::new(DefaultRequestHandler::new(
        ChunkerExecutor,
        InMemoryTaskStore::new(),
    ));
    let card_producer = Arc::new(StaticAgentCard::new(agent_card(host, port)));
    axum::Router::new()
        .merge(a2a_server::jsonrpc::jsonrpc_router(handler))
        .merge(a2a_server::agent_card::agent_card_router(card_producer))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fixtures;
    use crate::pyjson;
    use axum::body::Body;
    use axum::http::Request;
    use base64::Engine as _;
    use http_body_util::BodyExt as _;
    use serde_json::{Value, json};
    use tower::ServiceExt as _;

    const TP: &str = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01";

    fn send(parts: Value) -> Value {
        let message = json!({"messageId": new_message_id(), "contextId": "ctx", "role": "ROLE_USER", "parts": parts});
        json!({"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {"message": message}})
    }

    async fn post(payload: Value, traceparent: Option<&str>) -> Value {
        let mut request = Request::post("/")
            .header("content-type", "application/json")
            .header("A2A-Version", "1.0");
        if let Some(tp) = traceparent {
            request = request.header("traceparent", tp);
        }
        let request = request
            .body(Body::from(payload.to_string()))
            .expect("request");
        let response = app("localhost", 8000)
            .oneshot(request)
            .await
            .expect("response");
        assert_eq!(response.status(), 200);
        let bytes = response
            .into_body()
            .collect()
            .await
            .expect("body")
            .to_bytes();
        let body: Value = serde_json::from_slice(&bytes).expect("JSON body");
        assert!(body.get("error").is_none(), "{body}");
        body["result"]["task"].clone()
    }

    fn reply(task: &Value) -> String {
        task["status"]["message"]["parts"][0]["text"]
            .as_str()
            .unwrap_or_default()
            .to_owned()
    }

    #[tokio::test]
    async fn agent_card_route_serves_one_skill() {
        let request = Request::get("/.well-known/agent-card.json")
            .body(Body::empty())
            .expect("request");
        let response = app("localhost", 8000)
            .oneshot(request)
            .await
            .expect("response");
        let bytes = response
            .into_body()
            .collect()
            .await
            .expect("body")
            .to_bytes();
        let card: Value = serde_json::from_slice(&bytes).expect("JSON card");
        assert_eq!(card["name"], card::AGENT_NAME);
        assert_eq!(card["skills"].as_array().map(|s| s.len()), Some(1));
        assert_eq!(card["skills"][0]["name"], card::SKILL_NAME);
    }

    #[tokio::test]
    async fn a_split_turn_has_text_and_data_parts_and_ignores_policy() {
        let text = fixtures::transcript(12_000, 7);
        let parts = json!([{"text": format!("user: hi\nassistant: hello\n\nCurrent message: split into 2 parts by turns\n{text}")},
                           {"text": "## Organization Policy\nAlways answer in French."}]);
        let task = post(send(parts), Some(TP)).await;
        assert_eq!(task["status"]["state"], "TASK_STATE_COMPLETED");
        assert!(
            reply(&task).contains("into 2 parts (by turns") && !reply(&task).contains("French")
        );
        let artifact = &task["artifacts"][0]["parts"];
        assert_eq!(artifact[0]["text"], reply(&task).as_str());
        let numbers: Vec<f64> = artifact[1]["data"]["items"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|p| p["n"].as_f64())
            .collect();
        assert_eq!(
            numbers,
            [1.0, 2.0],
            "data-part numbers travel as protobuf Struct floats"
        );
    }

    #[tokio::test]
    async fn a_text_file_part_is_split() {
        let raw = base64::engine::general_purpose::STANDARD.encode(fixtures::transcript(9_000, 7));
        let parts = json!([{"text": "split into 2 parts"}, {"raw": raw, "filename": "call.txt", "mediaType": "text/plain"}]);
        let task = post(send(parts), None).await;
        assert_eq!(task["status"]["state"], "TASK_STATE_COMPLETED");
        assert!(reply(&task).contains("into 2 parts"));
    }

    #[tokio::test]
    async fn a_short_merge_completes_and_a_fresh_app_finishes_it_from_history() {
        let first = format!("merge these: {}", pyjson::dumps(&fixtures::ranked_1()));
        let short = post(send(json!([{"text": first}])), None).await;
        assert_eq!(short["status"]["state"], "TASK_STATE_COMPLETED");
        assert!(reply(&short).contains("at least two JSON results"));
        let turns = format!("user: {first}\nassistant: {}", reply(&short));
        let second = pyjson::dumps(&fixtures::ranked_2());
        let done = post(
            send(json!([{"text": format!("{turns}\n\nCurrent message: {second}")}])),
            None,
        )
        .await; // another replica
        assert_eq!(done["status"]["state"], "TASK_STATE_COMPLETED");
        assert!(reply(&done).contains("Merged 2 results"));
        assert_eq!(
            done["artifacts"][0]["parts"][1]["data"]["merged_parts"].as_f64(),
            Some(2.0)
        );
        let later = format!("{turns}\nuser: {second}\nassistant: {}", reply(&done));
        let notes = post(
            send(json!([{"text": format!("{later}\n\nCurrent message: show the merge notes")}])),
            None,
        )
        .await;
        assert!(reply(&notes).contains("re-numbered"));
    }

    #[tokio::test]
    async fn every_kind_of_message_ends_completed() {
        let texts = [
            "hi",
            "what's 2+2?",
            "show part 2",
            "split into 3 parts",
            "merge these: {\"a\": 1}",
            "that's all",
            "part 1 of 2, more coming",
            "as json",
            "merge with this {",
            "",
        ];
        for text in texts {
            let task = post(send(json!([{"text": text}])), None).await;
            assert_eq!(task["status"]["state"], "TASK_STATE_COMPLETED", "{text}");
        }
    }
}
