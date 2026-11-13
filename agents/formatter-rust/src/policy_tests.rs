//! Test-only: the package rules, checked over the files themselves. No state API, no pause, no model
//! client, no gateway, self-contained, and the card file matches the code.

use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::card;

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn read(path: &Path) -> String {
    std::fs::read_to_string(path).unwrap_or_else(|e| panic!("{}: {e}", path.display()))
}

fn sources() -> Vec<(String, String)> {
    let dir = root().join("src");
    let mut files: Vec<(String, String)> = std::fs::read_dir(&dir)
        .expect("src dir")
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|x| x == "rs"))
        .map(|p| {
            (
                p.file_name()
                    .map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_default(),
                read(&p),
            )
        })
        .collect();
    files.sort();
    files
}

/// Banned words are built at run time so this file does not trip its own scan.
fn word(parts: &[&str]) -> String {
    parts.concat()
}

#[test]
fn nothing_references_the_platform_state_api() {
    let banned = [
        word(&["/api/agent", "-state"]),
        word(&["agent", "-state"]),
        word(&["NASIKO_", "STATE_URL"]),
        word(&["NASIKO_", "AGENT_TOKEN"]),
    ];
    let mut files = sources();
    files.push(("Dockerfile".into(), read(&root().join("Dockerfile"))));
    files.push(("Cargo.toml".into(), read(&root().join("Cargo.toml"))));
    for (name, text) in files {
        for b in &banned {
            assert!(!text.contains(b.as_str()), "{name} mentions {b}");
        }
    }
}

#[test]
fn no_code_can_pause_a_task() {
    let banned = [
        word(&["Input", "Required"]),
        word(&["input", "_required"]),
        word(&["input", "-required"]),
        word(&["hi", "tl"]),
    ];
    for (name, text) in sources() {
        let lowered = text.to_lowercase();
        for b in &banned {
            assert!(
                !text.contains(b.as_str()) && !lowered.contains(&b.to_lowercase()),
                "{name} mentions {b}"
            );
        }
    }
}

#[test]
fn no_model_client_no_gateway_and_self_contained() {
    let manifest = read(&root().join("Cargo.toml"));
    for dep in ["reqwest", "async-openai", "path ="] {
        assert!(!manifest.contains(dep), "Cargo.toml has {dep}");
    }
    let platform_repo = word(&["nasiko", "-cloud"]);
    for (name, text) in sources() {
        for b in [
            word(&["OPENAI", "_"]),
            word(&["MCP_", "GATEWAY"]),
            platform_repo.clone(),
            word(&["agents/", "common"]),
        ] {
            assert!(!text.contains(b.as_str()), "{name} mentions {b}");
        }
    }
    for file in ["Dockerfile", "PLAN.md", "AgentCard.json"] {
        assert!(
            !read(&root().join(file)).contains(platform_repo.as_str()),
            "{file}"
        );
    }
    let docker = read(&root().join("Dockerfile"));
    assert!(
        docker.contains("COPY . .") && !docker.contains(".."),
        "the build context is this folder only"
    );
}

#[test]
fn agent_card_json_matches_code() {
    let data: Value =
        serde_json::from_str(&read(&root().join("AgentCard.json"))).expect("valid AgentCard.json");
    assert_eq!(data["name"], card::AGENT_NAME);
    assert_eq!(data["version"], card::AGENT_VERSION);
    assert_eq!(data["version"], env!("CARGO_PKG_VERSION"));
    assert_eq!(data["description"], card::AGENT_DESCRIPTION);
    assert_eq!(data["agentFramework"], "rust");
    assert_eq!(data["capabilities"][word(&["hi", "tl"])], false);
    assert_eq!(data["capabilities"]["chat_agent"], true);
    let skills = data["skills"].as_array().expect("skills");
    assert_eq!(skills.len(), 1);
    assert_eq!(skills[0]["name"], card::SKILL_NAME);
    assert_eq!(
        skills[0]["examples"],
        serde_json::json!(card::SKILL_EXAMPLES)
    );
    assert_eq!(
        data["defaultInputModes"],
        serde_json::json!(card::INPUT_MODES)
    );
}

#[test]
fn source_files_stay_small() {
    for (name, text) in sources() {
        assert!(text.lines().count() < 800, "{name} is too long");
    }
}
