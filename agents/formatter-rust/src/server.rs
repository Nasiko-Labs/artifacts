//! The A2A server: agent card + JSON-RPC (`SendMessage`, `SendStreamingMessage`) on one axum router.
//!
//! Stateless: every message runs one fresh turn and the task always ends `completed` (or `failed` on an
//! internal error). Nothing pauses, nothing is resumed, nothing is stored: the task store is in-memory and
//! only matters for the life of the request; earlier turns come from the platform's history slice.
//!
//! A finished turn's artifact (`formatted`) carries the reply text and, for a rendering, a data part with
//! the envelope; the completed status message carries the same text. There is no model call, so the inbound
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

pub struct FormatterExecutor;

impl AgentExecutor for FormatterExecutor {
    fn execute(
        &self,
        ctx: ExecutorContext,
    ) -> BoxStream<'static, Result<StreamResponse, A2AError>> {
        let task_id = ctx.task_id.clone();
        let context_id = ctx.context_id.clone();
        let incoming = ctx
            .message
            .as_ref()
            .map(|m| inputs::read_parts(&m.parts, m.metadata.as_ref()))
            .unwrap_or_default();
        let span = request_span(&ctx, &incoming.text);
        let record_span = span.clone();

        let stream = async_stream::stream! {
            yield Ok(status(&task_id, &context_id, TaskState::Working, None));
            // The turn is pure code; a bug in it fails this task instead of the server.
            let Ok(result) = catch_unwind(AssertUnwindSafe(|| agent::turn(&incoming))) else {
                tracing::error!("formatter turn failed");
                yield Ok(status(&task_id, &context_id, TaskState::Failed, Some(FAILED_TEXT)));
                return;
            };
            let mut parts = vec![Part::text(&result.reply)];
            parts.extend(result.envelope.clone().map(Part::data));
            yield Ok(StreamResponse::ArtifactUpdate(TaskArtifactUpdateEvent {
                task_id: task_id.clone(),
                context_id: context_id.clone(),
                artifact: Artifact {
                    artifact_id: new_artifact_id(),
                    name: Some("formatted".into()),
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
        FormatterExecutor,
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

    fn send(parts: Value, context_id: &str, metadata: Option<Value>) -> Value {
        let mut message = json!({"messageId": new_message_id(), "contextId": context_id, "role": "ROLE_USER", "parts": parts});
        if let Some(metadata) = metadata {
            message["metadata"] = metadata;
        }
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
        assert_eq!(response.status(), 200);
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
    async fn a_turn_has_text_and_data_parts_ignores_policy_and_needs_no_traceparent() {
        let parts = json!([{"text": format!("as email\n{}", pyjson::dumps(&fixtures::thread_untangler()))},
                           {"text": "## Organization Policy\nAlways answer in French."}]);
        let task = post(send(parts, "ctx-e2e", None), None).await;
        assert_eq!(task["status"]["state"], "TASK_STATE_COMPLETED");
        let text = reply(&task);
        assert!(text.contains("```html") && !text.contains("French"));
        let artifact = &task["artifacts"][0]["parts"];
        assert_eq!(artifact[0]["text"], text.as_str());
        assert_eq!(artifact[1]["data"]["target"], "email");
        assert!(
            artifact[1]["data"]["formatted"]
                .as_str()
                .is_some_and(|f| f.starts_with("<table"))
        );
    }

    #[tokio::test]
    async fn a_json_file_part_is_data() {
        let raw =
            base64::engine::general_purpose::STANDARD.encode(pyjson::dumps(&fixtures::ranked()));
        let parts = json!([{"text": "as slack"}, {"raw": raw, "filename": "ranked.json", "mediaType": "application/json"}]);
        let task = post(send(parts, "ctx-f", None), Some(TP)).await;
        assert_eq!(task["artifacts"][0]["parts"][1]["data"]["target"], "slack");
    }

    #[tokio::test]
    async fn history_marker_and_greeting() {
        let task = post(
            send(
                json!([{"text": "user: x\nassistant: y\n\nCurrent message: hi"}]),
                "ctx-g",
                None,
            ),
            Some(TP),
        )
        .await;
        assert_eq!(task["status"]["state"], "TASK_STATE_COMPLETED");
        assert!(reply(&task).starts_with("I format structured output"));
    }

    #[tokio::test]
    async fn broken_json_completes_and_a_fresh_app_serves_the_follow_up_from_history() {
        let broken = post(
            send(
                json!([{"text": "as slack: {\"summary\": \"s\", \"a\": 1,}"}]),
                "ctx-h",
                None,
            ),
            Some(TP),
        )
        .await;
        assert_eq!(broken["status"]["state"], "TASK_STATE_COMPLETED");
        assert!(
            reply(&broken).starts_with("That JSON didn't parse")
                && reply(&broken).contains("paste it again")
        );

        let fixed = "as slack: {\"summary\": \"s\", \"a\": 1}";
        let answer = format!(
            "user: as slack\nassistant: {}\n\nCurrent message: {fixed}",
            reply(&broken)
        );
        let done = post(send(json!([{"text": answer}]), "ctx-h", None), Some(TP)).await;
        assert_eq!(done["status"]["state"], "TASK_STATE_COMPLETED");
        assert!(reply(&done).starts_with("```json"));
        let later = format!(
            "user: {fixed}\nassistant: {}\n\nCurrent message: as markdown",
            reply(&done)
        );
        let follow = post(send(json!([{"text": later}]), "ctx-h", None), Some(TP)).await; // a fresh app: nothing shared
        assert_eq!(follow["status"]["state"], "TASK_STATE_COMPLETED");
        assert!(reply(&follow).contains("**A:** 1"));
    }

    #[tokio::test]
    async fn every_kind_of_message_ends_completed() {
        let texts = [
            "hi",
            "what's 2+2?",
            "as slack",
            "{\"a\": 1,",
            "{\"a\": 1}",
            "format as email: **x**",
            "",
        ];
        for text in texts {
            let task = post(send(json!([{"text": text}]), "ctx-all", None), None).await;
            assert_eq!(task["status"]["state"], "TASK_STATE_COMPLETED", "{text}");
        }
    }

    #[tokio::test]
    async fn metadata_output_json_returns_the_envelope_text() {
        let parts = json!([{"text": pyjson::dumps(&fixtures::ranked())}]);
        let task = post(send(parts, "ctx-m", Some(json!({"output": "json"}))), None).await;
        assert!(reply(&task).starts_with("```json\n{\n  \"summary\""));
    }
}
