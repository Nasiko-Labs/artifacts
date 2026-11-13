//! Tracing setup: OTLP span export when `OTEL_EXPORTER_OTLP_ENDPOINT` is set, plain log lines otherwise.
//! This agent makes no model call, so the only span it emits is the `invoke_agent` request span; it joins
//! the caller's W3C trace from the inbound `traceparent`.

use opentelemetry::KeyValue;
use opentelemetry::trace::TracerProvider as _;
use opentelemetry_otlp::WithExportConfig;
use opentelemetry_sdk::{
    Resource,
    trace::{RandomIdGenerator, Sampler, SdkTracerProvider},
};
use tracing_subscriber::{EnvFilter, layer::SubscriberExt, util::SubscriberInitExt};

pub fn service_name() -> String {
    std::env::var("OTEL_SERVICE_NAME").unwrap_or_else(|_| env!("CARGO_PKG_NAME").into())
}

/// Initialize tracing with OTLP export when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.
/// Falls back to a plain fmt subscriber (and says why) when the exporter cannot be built.
pub fn init() {
    let env_filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    let registry = tracing_subscriber::registry()
        .with(env_filter)
        .with(tracing_subscriber::fmt::layer());

    let Ok(endpoint) = std::env::var("OTEL_EXPORTER_OTLP_ENDPOINT") else {
        registry.init();
        return;
    };
    let exporter = opentelemetry_otlp::SpanExporter::builder()
        .with_tonic()
        .with_endpoint(endpoint)
        .build();
    let exporter = match exporter {
        Ok(exporter) => exporter,
        Err(error) => {
            registry.init();
            tracing::warn!(%error, "OTLP span exporter unavailable; spans are not exported");
            return;
        }
    };
    let resource = Resource::builder_empty()
        .with_attribute(KeyValue::new(
            opentelemetry_semantic_conventions::resource::SERVICE_NAME,
            service_name(),
        ))
        .build();
    let provider = SdkTracerProvider::builder()
        .with_batch_exporter(exporter)
        .with_sampler(Sampler::AlwaysOn)
        .with_id_generator(RandomIdGenerator::default())
        .with_resource(resource)
        .build();
    opentelemetry::global::set_tracer_provider(provider.clone());
    let otel_layer = tracing_opentelemetry::layer().with_tracer(provider.tracer(service_name()));
    registry.with(otel_layer).init();
}

/// Whether message content may be recorded on spans, per the platform-injected
/// `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`. Anything except an explicit opt-out means on.
pub fn capture_content() -> bool {
    match std::env::var("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT") {
        Ok(v) => !matches!(
            v.to_ascii_uppercase().as_str(),
            "NO_CONTENT" | "FALSE" | "0" | ""
        ),
        Err(_) => true,
    }
}

/// A single-message GenAI semconv array (`gen_ai.input.messages` / `gen_ai.output.messages`).
pub fn genai_text_message(role: &str, text: &str) -> serde_json::Value {
    serde_json::json!([{"role": role, "parts": [{"type": "text", "content": text}]}])
}

/// Parse a W3C `traceparent` header into a remote OTel context, so spans created under it join the
/// caller's trace. None for anything malformed (the span then starts a local root trace).
pub fn remote_context_from_traceparent(tp: &str) -> Option<opentelemetry::Context> {
    use opentelemetry::propagation::TextMapPropagator;
    use opentelemetry::trace::TraceContextExt;
    let carrier: std::collections::HashMap<String, String> =
        [("traceparent".to_string(), tp.to_string())].into();
    let ctx = opentelemetry_sdk::propagation::TraceContextPropagator::new().extract(&carrier);
    ctx.span().span_context().is_valid().then_some(ctx)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn traceparent_parsing() {
        assert!(
            remote_context_from_traceparent(
                "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
            )
            .is_some()
        );
        assert!(remote_context_from_traceparent("garbage").is_none());
    }
}
