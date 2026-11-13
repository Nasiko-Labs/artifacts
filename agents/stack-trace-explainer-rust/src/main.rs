//! Binary entry point: telemetry, the model client from the router env, then the A2A server on
//! 0.0.0.0:8000 (`PORT` or `--port N` overrides). Nothing here calls a model.

use std::sync::Arc;

use stack_trace_explainer_rust::{llm::LlmClient, server, telemetry};

const DEFAULT_PORT: u16 = 8000;

fn port() -> u16 {
    let args: Vec<String> = std::env::args().collect();
    let from_args = args
        .iter()
        .position(|a| a == "--port")
        .and_then(|i| args.get(i + 1))
        .and_then(|p| p.parse().ok());
    from_args
        .or_else(|| std::env::var("PORT").ok().and_then(|p| p.parse().ok()))
        .unwrap_or(DEFAULT_PORT)
}

#[tokio::main]
async fn main() {
    telemetry::init();
    let port = port();
    let llm = Arc::new(LlmClient::from_env());
    let app = server::build_app(llm, port);
    let listener = match tokio::net::TcpListener::bind(("0.0.0.0", port)).await {
        Ok(listener) => listener,
        Err(err) => {
            tracing::error!("cannot bind 0.0.0.0:{port}: {err}");
            std::process::exit(1);
        }
    };
    tracing::info!("stack-trace-explainer-rust listening on 0.0.0.0:{port}");
    if let Err(err) = axum::serve(listener, app).await {
        tracing::error!("server stopped: {err}");
        std::process::exit(1);
    }
}
