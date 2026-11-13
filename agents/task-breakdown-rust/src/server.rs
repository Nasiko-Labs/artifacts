//! The A2A server: card + JSON-RPC (`SendMessage`, `SendStreamingMessage`) on one axum router.
//!
//! Stateless: every message runs the turn once and its task ends `completed` (or `failed` when the turn
//! itself breaks). Nothing pauses: no task is ever left waiting for more input. The task store is a2a-rs's
//! in-memory one, used only so the handler can return the finished task; finished tasks are purged.
//!
//! A finished turn's artifact carries the reply text (markdown, or fenced JSON when asked) and, when
//! there is one, a data part with the full envelope for API clients.

use std::panic::AssertUnwindSafe;
use std::sync::Arc;
use std::time::Duration;

use a2a::*;
use a2a_server::*;
use futures::FutureExt as _;
use futures::stream::BoxStream;

use crate::card;
use crate::inputs::read_message;
use crate::llm::{LlmClient, TURN_DEADLINE, TurnDeps};
use crate::telemetry;
use crate::turns::{self, TurnResult};

pub const FAILED_TEXT: &str =
    "I couldn't finish this request: a platform call failed. Please try again.";
/// Finished tasks kept in memory only so the handler can return them; the oldest are purged.
const FINISHED_TASKS_KEPT: usize = 64;

pub struct Executor {
    llm: Arc<LlmClient>,
    deadline: Duration,
}

impl Executor {
    pub fn new(llm: Arc<LlmClient>) -> Self {
        Self {
            llm,
            deadline: TURN_DEADLINE,
        }
    }
}

fn header(ctx: &ExecutorContext, name: &str) -> Option<String> {
    ctx.service_params
        .get(name)
        .and_then(|v| v.first())
        .cloned()
}

impl AgentExecutor for Executor {
    fn execute(
        &self,
        ctx: ExecutorContext,
    ) -> BoxStream<'static, Result<StreamResponse, A2AError>> {
        let traceparent = header(&ctx, "traceparent");
        let deps = TurnDeps::new(self.llm.clone(), traceparent, self.deadline);
        let incoming = read_message(ctx.message.as_ref());
        let (task_id, context_id) = ctx.task_info();
        let span = tracing::info_span!(
            "a2a.execute",
            otel.name = %format!("{}.request", card::AGENT_NAME),
            otel.kind = "server",
            gen_ai.operation.name = "invoke_agent",
            gen_ai.agent.name = card::AGENT_NAME,
            session.id = %context_id,
            agent.llm_calls = tracing::field::Empty,
            gen_ai.input.messages = tracing::field::Empty,
            gen_ai.output.messages = tracing::field::Empty,
        );
        if let Some(cx) = &deps.parent_cx {
            use tracing_opentelemetry::OpenTelemetrySpanExt as _;
            span.set_parent(cx.clone());
        }
        let capture = telemetry::capture_content();
        if capture && !incoming.text.is_empty() {
            let input = telemetry::genai_text_message("user", &incoming.text).to_string();
            span.record("gen_ai.input.messages", input.as_str());
        }
        let record = span.clone();
        let stream = async_stream::stream! {
            yield Ok(status(&task_id, &context_id, TaskState::Working, None));
            let outcome = AssertUnwindSafe(turns::turn(&incoming, &deps)).catch_unwind().await;
            record.record("agent.llm_calls", deps.calls());
            let Ok(result) = outcome else {
                tracing::error!("turn panicked");
                yield Ok(status(&task_id, &context_id, TaskState::Failed, Some(FAILED_TEXT)));
                return;
            };
            if capture {
                let output = telemetry::genai_text_message("assistant", &result.reply).to_string();
                record.record("gen_ai.output.messages", output.as_str());
            }
            yield Ok(artifact(&task_id, &context_id, &result));
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

fn agent_message(task_id: &str, context_id: &str, text: &str) -> Message {
    Message {
        message_id: new_message_id(),
        context_id: Some(context_id.into()),
        task_id: Some(task_id.into()),
        role: Role::Agent,
        parts: vec![Part::text(text)],
        metadata: None,
        extensions: None,
        reference_task_ids: None,
    }
}

fn status(task_id: &str, context_id: &str, state: TaskState, text: Option<&str>) -> StreamResponse {
    StreamResponse::StatusUpdate(TaskStatusUpdateEvent {
        task_id: task_id.into(),
        context_id: context_id.into(),
        status: TaskStatus {
            state,
            message: text.map(|t| agent_message(task_id, context_id, t)),
            timestamp: Some(chrono::Utc::now()),
        },
        metadata: None,
    })
}

fn artifact(task_id: &str, context_id: &str, result: &TurnResult) -> StreamResponse {
    let mut parts = vec![Part::text(&result.reply)];
    if let Some(data) = result.data.clone().filter(|d| !d.is_null()) {
        parts.push(Part::data(data));
    }
    StreamResponse::ArtifactUpdate(TaskArtifactUpdateEvent {
        task_id: task_id.into(),
        context_id: context_id.into(),
        artifact: Artifact {
            artifact_id: new_artifact_id(),
            name: Some(card::AGENT_NAME.into()),
            description: None,
            parts,
            metadata: None,
            extensions: None,
        },
        append: Some(false),
        last_chunk: Some(true),
        metadata: None,
    })
}

/// The full app: JSON-RPC at `/` and the card at `/.well-known/agent-card.json`.
pub fn build_app(llm: Arc<LlmClient>, port: u16) -> axum::Router {
    let store = InMemoryTaskStore::new().with_terminal_task_limit(FINISHED_TASKS_KEPT);
    let handler = Arc::new(DefaultRequestHandler::new(Executor::new(llm), store));
    let card_producer = Arc::new(StaticAgentCard::new(card::agent_card(port)));
    axum::Router::new()
        .merge(a2a_server::jsonrpc::jsonrpc_router(handler))
        .merge(a2a_server::agent_card::agent_card_router(card_producer))
}
