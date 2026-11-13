//! The LLM-backed steps: a new plan (map when chunked, plan, one repair) and a revision (revise, one
//! repair). Everything after the model's JSON is code: normalise, list problems, reconcile ids, fix,
//! finalize, and fit a timebox.

use serde_json::{Map, Value, json};

use crate::checks::{self, Draft};
use crate::finalize::{Context, finalize};
use crate::fit;
use crate::inputs::{Brief, band, chunk, deadline, is_replan, team_size};
use crate::llm::{LlmError, MAX_TOKENS, TurnDeps};
use crate::mapping;
use crate::models::Plan;
use crate::prompts::{self, PlanAsk};
use crate::revision;
use crate::util::{dedupe, thousands};

const STATUSES: &[&str] = &["ok", "off_topic", "needs_info"];
pub const REVISION_BAND: (usize, usize) = (1, 40);
const CODE_CONSTRAINT: &[&str] = &["Team of ", "Deadline: "];
pub const REPLANNED_IN_CODE: &str = "Re-planned in code for your team size and timebox.";

#[derive(Debug, Clone, PartialEq)]
pub enum Outcome {
    Ok(Box<Plan>),
    OffTopic,
    NeedsInfo,
    /// The model could not be reached in time (transport, timeout, deadline, budget).
    Failed,
    /// The model answered, but never with JSON that fits the contract.
    Unusable,
}

/// The model's answer, if it fits the contract: a known status, and tasks when the status is ok.
fn valid(data: &Value) -> Option<Map<String, Value>> {
    let map = data.as_object()?;
    let status = match map.get("status") {
        None => "ok",
        Some(s) => s.as_str().filter(|s| STATUSES.contains(s))?,
    };
    let has_tasks = map
        .get("tasks")
        .and_then(Value::as_array)
        .is_some_and(|t| !t.is_empty());
    if status == "ok" && !has_tasks {
        return None;
    }
    let mut out = map.clone();
    out.insert("status".into(), json!(status));
    Some(out)
}

fn status_of(data: &Map<String, Value>) -> &str {
    data.get("status").and_then(Value::as_str).unwrap_or("ok")
}

/// The call, and one retry from the repair budget when the answer is not valid JSON for the contract.
async fn ask(deps: &TurnDeps, messages: &[Value]) -> Result<Option<Map<String, Value>>, LlmError> {
    let data = deps.ask_json(messages, valid, MAX_TOKENS).await?;
    if data.is_none() && deps.has_time() && deps.take_repair() {
        return deps.ask_json(messages, valid, MAX_TOKENS).await;
    }
    Ok(data)
}

async fn with_repair(
    deps: &TurnDeps,
    draft: Draft,
    band: (usize, usize),
    policy: &[String],
) -> Draft {
    let found = checks::problems(&draft, band, policy);
    if found.is_empty() || !deps.has_time() || !deps.take_repair() {
        return draft;
    }
    let messages = prompts::repair_messages(&draft.to_json(), &found, policy);
    let Ok(Some(data)) = deps.ask_json(&messages, valid, MAX_TOKENS).await else {
        return draft;
    };
    if status_of(&data) != "ok" {
        return draft;
    }
    let repaired = checks::normalize(&data);
    if repaired.tasks.is_empty() {
        draft
    } else {
        repaired
    }
}

fn non_ok(status: &str) -> Outcome {
    match status {
        "off_topic" => Outcome::OffTopic,
        _ => Outcome::NeedsInfo,
    }
}

/// Never asks back (the stateless agent cannot pause): the model plans with what it has and lists its
/// assumptions.
pub async fn new_plan(deps: &TurnDeps, brief: &Brief, policy: &[String]) -> Outcome {
    let chunks = chunk(&brief.spec);
    let mut notes: Vec<String> = Vec::new();
    let mut requirements: Vec<String> = Vec::new();
    let mut spec = brief.spec.as_str();
    if brief.spec_cut > 0 {
        notes.push(format!(
            "Your message was {} characters over the 100,000 I read; the rest wasn't used.",
            thousands(brief.spec_cut)
        ));
    }
    if chunks.len() > 1 {
        let (found, answered) = mapping::requirements(deps, &chunks).await;
        requirements = found;
        spec = "";
        notes.push(if answered == chunks.len() {
            format!("Planned from the whole spec ({} parts).", chunks.len())
        } else {
            format!(
                "Planned from {answered} of {} parts of the spec; the others could not be read.",
                chunks.len()
            )
        });
    }
    let plan_band = band(&brief.granularity);
    let messages = prompts::plan_messages(&PlanAsk {
        goal: &brief.goal,
        spec,
        requirements: &requirements,
        constraints: &[],
        band: plan_band,
        policy,
        may_ask: false,
    });
    let data = match ask(deps, &messages).await {
        Err(_) => return Outcome::Failed,
        Ok(None) => return Outcome::Unusable,
        Ok(Some(data)) => data,
    };
    if status_of(&data) != "ok" {
        return non_ok(status_of(&data));
    }
    let draft = with_repair(deps, checks::normalize(&data), plan_band, policy).await;
    let ctx = Context {
        policy: policy.to_vec(),
        deadline: brief.deadline.clone(),
        team_size: brief.team_size,
        chunks: if chunks.len() > 1 { chunks.len() } else { 0 },
        notes,
        ..Context::new(&brief.goal, &brief.source(), plan_band)
    };
    Outcome::Ok(Box::new(Plan {
        granularity: brief.granularity.clone(),
        ..finalize(&draft, &ctx)
    }))
}

/// The plan as the model sees it (no status, no code-only fields).
pub fn plan_for_model(plan: &Plan) -> Value {
    let tasks: Vec<Value> = plan
        .tasks
        .iter()
        .map(|t| {
            json!({"id": t.id, "phase": t.phase, "title": t.title, "size": t.size, "depends_on": t.depends_on,
                   "acceptance": t.acceptance, "owner": t.owner, "source": t.source})
        })
        .collect();
    json!({
        "goal": plan.goal,
        "phases": plan.phases.iter().map(|p| json!({"id": p.number, "name": p.name})).collect::<Vec<_>>(),
        "tasks": tasks,
        "constraints": plan.constraints,
        "risks": plan.risks.iter().map(|r| json!({"task": r.task, "text": r.text})).collect::<Vec<_>>(),
        "assumptions": plan.assumptions.iter().map(|a| json!({"text": a.text, "based_on": a.based_on})).collect::<Vec<_>>(),
    })
}

fn own_constraints(plan: &Plan) -> Vec<String> {
    plan.constraints
        .iter()
        .filter(|c| !CODE_CONSTRAINT.iter().any(|p| c.starts_with(p)))
        .cloned()
        .collect()
}

pub async fn revise(deps: &TurnDeps, plan: &Plan, instruction: &str, policy: &[String]) -> Outcome {
    let messages =
        prompts::revise_messages(&plan_for_model(plan), instruction, REVISION_BAND, policy);
    let data = match ask(deps, &messages).await {
        Err(_) => return Outcome::Failed,
        Ok(None) => return Outcome::Unusable,
        Ok(Some(data)) => data,
    };
    let status = status_of(&data);
    if status == "off_topic" && is_replan(instruction) {
        return Outcome::Ok(Box::new(fit::fit(replanned_in_code(plan, instruction))));
    }
    if status != "ok" {
        return non_ok(status);
    }
    let (draft, _, first_notes) = revision::reconcile(plan, &checks::normalize(&data), instruction);
    let draft = with_repair(deps, draft, REVISION_BAND, policy).await;
    let (draft, next_id, notes) = revision::reconcile(plan, &draft, instruction);
    let due = deadline(instruction);
    let ctx = Context {
        policy: policy.to_vec(),
        renumber: false,
        next_id,
        known_constraints: own_constraints(plan),
        deadline: if due.is_empty() {
            plan.deadline.clone()
        } else {
            due
        },
        team_size: team_size(instruction).or(plan.team_size),
        rate: plan.rate,
        chunks: plan.chunks,
        vague_used: plan.vague_used,
        notes: dedupe(first_notes.into_iter().chain(notes)),
        ..Context::new(
            &plan.goal,
            &format!("{}\n{instruction}", plan.source),
            REVISION_BAND,
        )
    };
    let revised = Plan {
        granularity: plan.granularity.clone(),
        ..finalize(&draft, &ctx)
    };
    Outcome::Ok(Box::new(if is_replan(instruction) {
        fit::fit(revised)
    } else {
        revised
    }))
}

/// A team-size/timebox re-plan the model declined: apply what code can (team lanes, the timebox as typed)
/// to the plan the user already has, rather than refusing an in-scope request.
fn replanned_in_code(plan: &Plan, instruction: &str) -> Plan {
    let people = team_size(instruction).or(plan.team_size);
    let due = match deadline(instruction) {
        d if d.is_empty() => plan.deadline.clone(),
        d => d,
    };
    let mut constraints: Vec<String> = Vec::new();
    if let Some(n) = people {
        constraints.push(format!("Team of {n}"));
    }
    if !due.is_empty() {
        constraints.push(format!("Deadline: {due} (as you wrote it)"));
    }
    constraints.extend(own_constraints(plan));
    Plan {
        team_size: people,
        deadline: due,
        constraints,
        notes: vec![REPLANNED_IN_CODE.into()],
        ..plan.clone()
    }
}
