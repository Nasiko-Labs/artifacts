//! The one LLM call wrapper: plain reqwest to an OpenAI-compatible `/chat/completions`, the inbound
//! `traceparent` forwarded on every request, a per-call timeout under the turn deadline, JSON mode,
//! and a per-turn call budget. Each call is a GenAI-semconv span carrying the model, the operation
//! and the token usage.

use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use serde_json::{Value, json};
use tokio::time::Instant;
use tracing::Instrument as _;

use crate::telemetry;

pub const DEFAULT_BASE_URL: &str = "https://api.openai.com/v1";
pub const DEFAULT_MODEL: &str = "gpt-4o-mini";
pub const CALL_TIMEOUT: Duration = Duration::from_secs(60);
pub const TURN_DEADLINE: Duration = Duration::from_secs(240);
pub const MIN_CALL_TIME: Duration = Duration::from_secs(5);
pub const MAX_CALLS_PER_TURN: usize = 4;
pub const MAX_TOKENS: u32 = 1_400;
pub const TEMPERATURE: f64 = 0.1;

/// Why a call did not produce an answer. Only `Transport` means the model could not be reached.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LlmError {
    NoTraceparent,
    OutOfTime,
    BudgetSpent,
    /// Network failure, timeout or a non-2xx status.
    Transport(String),
    /// A 2xx response that is not a chat completion.
    BadResponse(String),
}

impl LlmError {
    pub fn is_transport(&self) -> bool {
        matches!(self, LlmError::Transport(_))
    }
}

/// Process-wide: the HTTP client and the router settings, read once at startup. Building it makes
/// no call.
#[derive(Debug, Clone)]
pub struct LlmClient {
    http: reqwest::Client,
    base_url: String,
    api_key: String,
    pub model: String,
}

impl LlmClient {
    pub fn new(base_url: &str, api_key: &str, model: &str) -> Self {
        Self {
            http: reqwest::Client::new(),
            base_url: base_url.trim_end_matches('/').to_string(),
            api_key: api_key.to_string(),
            model: model.to_string(),
        }
    }

    /// `OPENAI_BASE_URL` / `OPENAI_API_KEY` are injected by the platform's LLM router; the model is
    /// `OPENAI_MODEL`, else `MODEL`, else the default.
    pub fn from_env() -> Self {
        let env = |key: &str| std::env::var(key).ok().filter(|v| !v.trim().is_empty());
        let model = env("OPENAI_MODEL")
            .or_else(|| env("MODEL"))
            .unwrap_or_else(|| DEFAULT_MODEL.into());
        let base = env("OPENAI_BASE_URL").unwrap_or_else(|| DEFAULT_BASE_URL.into());
        Self::new(
            &base,
            &env("OPENAI_API_KEY").unwrap_or_else(|| "unset".into()),
            &model,
        )
    }
}

/// Built per request: `traceparent` is this request's inbound header, never a cached one.
pub struct TurnDeps {
    pub llm: Arc<LlmClient>,
    pub traceparent: Option<String>,
    pub parent_cx: Option<opentelemetry::Context>,
    deadline: Instant,
    max_calls: usize,
    calls: AtomicUsize,
}

impl TurnDeps {
    pub fn new(llm: Arc<LlmClient>, traceparent: Option<String>, budget: Duration) -> Self {
        let traceparent = traceparent.filter(|t| !t.trim().is_empty());
        let parent_cx = traceparent
            .as_deref()
            .and_then(telemetry::remote_context_from_traceparent);
        Self {
            llm,
            traceparent,
            parent_cx,
            deadline: Instant::now() + budget,
            max_calls: MAX_CALLS_PER_TURN,
            calls: AtomicUsize::new(0),
        }
    }

    pub fn calls(&self) -> usize {
        self.calls.load(Ordering::SeqCst)
    }

    pub fn left(&self) -> usize {
        self.max_calls.saturating_sub(self.calls())
    }

    pub fn remaining(&self) -> Duration {
        self.deadline.saturating_duration_since(Instant::now())
    }

    pub fn has_time(&self) -> bool {
        self.remaining() >= MIN_CALL_TIME
    }

    pub fn can_call(&self) -> bool {
        self.left() > 0 && self.has_time()
    }

    fn take_call(&self) -> Result<(), LlmError> {
        self.calls
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |n| {
                (n < self.max_calls).then_some(n + 1)
            })
            .map(|_| ())
            .map_err(|_| LlmError::BudgetSpent)
    }

    /// One call in JSON mode; the raw text of the first choice.
    pub async fn call(&self, messages: &[Value]) -> Result<String, LlmError> {
        let Some(traceparent) = self.traceparent.clone() else {
            return Err(LlmError::NoTraceparent); // callers check first; never call the router without it
        };
        if !self.has_time() {
            return Err(LlmError::OutOfTime);
        }
        self.take_call()?;
        let timeout = CALL_TIMEOUT
            .min(self.remaining())
            .max(Duration::from_secs(1));
        let span = tracing::info_span!(
            "chat",
            otel.name = %format!("chat {}", self.llm.model),
            otel.kind = "client",
            gen_ai.operation.name = "chat",
            gen_ai.provider.name = "openai",
            gen_ai.request.model = %self.llm.model,
            gen_ai.request.max_tokens = MAX_TOKENS,
            gen_ai.request.temperature = TEMPERATURE,
            gen_ai.response.model = tracing::field::Empty,
            gen_ai.usage.input_tokens = tracing::field::Empty,
            gen_ai.usage.output_tokens = tracing::field::Empty,
            gen_ai.input.messages = tracing::field::Empty,
            gen_ai.output.messages = tracing::field::Empty,
        );
        if let Some(cx) = &self.parent_cx {
            use tracing_opentelemetry::OpenTelemetrySpanExt as _;
            span.set_parent(cx.clone());
        }
        self.post(messages, &traceparent, timeout)
            .instrument(span)
            .await
    }

    async fn post(
        &self,
        messages: &[Value],
        traceparent: &str,
        timeout: Duration,
    ) -> Result<String, LlmError> {
        let span = tracing::Span::current();
        let capture = telemetry::capture_content();
        if capture {
            span.record(
                "gen_ai.input.messages",
                telemetry::genai_input_messages(messages)
                    .to_string()
                    .as_str(),
            );
        }
        let body = json!({
            "model": self.llm.model,
            "messages": messages,
            "max_tokens": MAX_TOKENS,
            "temperature": TEMPERATURE,
            "response_format": {"type": "json_object"},
        });
        let response = self
            .llm
            .http
            .post(format!("{}/chat/completions", self.llm.base_url))
            .bearer_auth(&self.llm.api_key)
            .header("traceparent", traceparent)
            .timeout(timeout)
            .json(&body)
            .send()
            .await
            .map_err(|e| transport(&format!("request failed: {}", short_error(&e))))?;
        let status = response.status();
        if !status.is_success() {
            return Err(transport(&format!("model router answered HTTP {status}")));
        }
        let parsed: Value = response.json().await.map_err(|e| {
            LlmError::BadResponse(format!("not a chat completion: {}", short_error(&e)))
        })?;
        record_usage(&span, &parsed);
        let content = parsed["choices"][0]["message"]["content"]
            .as_str()
            .unwrap_or("")
            .to_string();
        if capture {
            let out = telemetry::genai_output_message(&content, &[], "stop");
            span.record("gen_ai.output.messages", out.to_string().as_str());
        }
        Ok(content)
    }

    /// One call, parsed as a JSON object; `Ok(None)` when the answer is not one.
    pub async fn call_json(&self, messages: &[Value]) -> Result<Option<Value>, LlmError> {
        let raw = self.call(messages).await?;
        Ok(load(&raw).filter(Value::is_object))
    }
}

fn transport(message: &str) -> LlmError {
    tracing::warn!("model call failed: {message}");
    LlmError::Transport(message.to_string())
}

fn short_error(err: &reqwest::Error) -> &'static str {
    if err.is_timeout() {
        "timeout"
    } else if err.is_connect() {
        "connect"
    } else if err.is_decode() {
        "decode"
    } else {
        "http"
    }
}

fn record_usage(span: &tracing::Span, response: &Value) {
    if let Some(model) = response.get("model").and_then(Value::as_str) {
        span.record("gen_ai.response.model", model);
    }
    let usage = &response["usage"];
    if let Some(v) = usage.get("prompt_tokens").and_then(Value::as_u64) {
        span.record("gen_ai.usage.input_tokens", v);
    }
    if let Some(v) = usage.get("completion_tokens").and_then(Value::as_u64) {
        span.record("gen_ai.usage.output_tokens", v);
    }
}

/// The model's text as JSON, tolerating a ```json fence around it.
pub fn load(raw: &str) -> Option<Value> {
    let mut text = raw.trim();
    if text.starts_with("```") {
        text = text.trim_matches('`');
        text = text.strip_prefix("json").unwrap_or(text).trim();
    }
    serde_json::from_str(text).ok()
}
