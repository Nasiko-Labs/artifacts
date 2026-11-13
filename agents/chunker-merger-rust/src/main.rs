//! chunker-merger-rust: splits a long text into numbered, ready-to-paste chunk prompts (losslessly) and
//! merges same-schema JSON results from the parts back into one, in code.
//!
//! No model, no tools, no gateway, no network calls: nothing here calls anything at startup or per turn.
//! Stateless: an in-memory task store that only matters for the request, no platform state API, so any
//! replica can answer any message.

mod actions;
mod agent;
mod card;
mod classify;
mod dedup;
mod envelope;
mod followup;
mod history;
mod inputs;
mod merge;
mod prompts_out;
mod pyjson;
mod recall;
mod render;
mod replies;
mod server;
mod smalltalk;
mod split;
mod splitters;
mod telemetry;
mod textidx;
mod verify;

#[cfg(test)]
mod fixtures;
#[cfg(test)]
mod policy_tests;

const DEFAULT_HOST: &str = "0.0.0.0";
const DEFAULT_PORT: u16 = 8000;

/// `--host H` and `--port N` (defaults 0.0.0.0 and 8000); anything else is ignored.
fn bind_from_args(args: &[String]) -> Result<(String, u16), String> {
    let mut host = DEFAULT_HOST.to_owned();
    let mut port = DEFAULT_PORT;
    let mut it = args.iter();
    while let Some(arg) = it.next() {
        match arg.as_str() {
            "--host" => host = it.next().ok_or("--host needs a value")?.clone(),
            "--port" => {
                let value = it.next().ok_or("--port needs a value")?;
                port = value
                    .parse()
                    .map_err(|_| format!("--port must be a number, got {value:?}"))?;
            }
            _ => {}
        }
    }
    Ok((host, port))
}

#[tokio::main]
async fn main() {
    telemetry::init();
    let args: Vec<String> = std::env::args().skip(1).collect();
    let (host, port) = match bind_from_args(&args) {
        Ok(bind) => bind,
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(2);
        }
    };
    let listener = match tokio::net::TcpListener::bind((host.as_str(), port)).await {
        Ok(listener) => listener,
        Err(error) => {
            tracing::error!(%error, "could not bind {host}:{port}");
            std::process::exit(1);
        }
    };
    tracing::info!("{} listening on {host}:{port}", card::AGENT_NAME);
    if let Err(error) = axum::serve(listener, server::app(&host, port)).await {
        tracing::error!(%error, "server stopped");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bind_defaults_and_flags() {
        assert_eq!(bind_from_args(&[]), Ok(("0.0.0.0".to_owned(), 8000)));
        let args: Vec<String> = ["--port", "9123"].map(String::from).to_vec();
        assert_eq!(bind_from_args(&args), Ok(("0.0.0.0".to_owned(), 9123)));
        assert!(bind_from_args(&["--port".to_owned(), "x".to_owned()]).is_err());
    }
}
