"""Pure: the IAM analysis (statement table, the "can it…?" probe, gotchas I01–I18). Static only: no
condition evaluation, no SCPs or permission boundaries, no live action list."""

from __future__ import annotations

from collections import Counter

import iam
from analysis import Analysis
from gotchas import Gotcha, ordered
from iam import Policy, PolicyError, Statement, action_matches
from iam_tables import DATA_READS, ESCALATION, RESOURCE_SCOPED, SERVICE_PREFIXES
from probes import Probes

HONESTY = ("I don't evaluate conditions, organization SCPs or permission boundaries, and action names are not "
           "checked against the live AWS list.")
OPEN_CIDRS = ("0.0.0.0/0", "::/0")


def analyze(config: str, probes: Probes) -> Analysis:
    try:
        policy = iam.parse(config)
    except PolicyError as exc:
        return Analysis("iam", config, {}, parse_error=str(exc), summary=f"IAM policy: can't read it ({exc}).")
    statements = policy.statements
    allows = sum(1 for s in statements if s.effect.casefold() == "allow")
    facts = {"version": policy.version or "missing", "allow_statements": allows,
             "deny_statements": len(statements) - allows,
             "statements": [iam.statement_facts(s) for s in statements]}
    checked: list[str] = []
    if probes.action:
        answer = iam.evaluate(policy, probes.action, probes.resource)
        facts["probe"] = answer
        checked.append(answer)
    gotchas = ordered(policy_gotchas(policy))
    high = sum(1 for g in gotchas if g.severity == "high")
    summary = (f"IAM policy: {len(statements)} statements ({allows} Allow, {len(statements) - allows} Deny); "
               f"{len(gotchas)} gotchas, {high} high.")
    return Analysis("iam", config, facts, tuple(gotchas), checked=tuple(checked), summary=summary,
                    must_mention=tuple(f"Statement {s.index}" for s in statements), notes=(HONESTY,))


def _allow(s: Statement) -> bool:
    return s.effect.casefold() == "allow"


def _grants(s: Statement, action: str) -> bool:
    return _allow(s) and iam.covers_action(s, action)


def _star_resource(s: Statement) -> bool:
    return "*" in s.resources


def policy_gotchas(policy: Policy) -> list[Gotcha]:
    found: list[Gotcha] = []
    for s in policy.statements:
        found += _statement_gotchas(s)
    found += _policy_level(policy)
    return found


def _statement_gotchas(s: Statement) -> list[Gotcha]:
    found: list[Gotcha] = []
    label = s.label
    if _allow(s) and "*" in s.actions and _star_resource(s):
        found.append(Gotcha("I01", "high", f"{label} allows every action on every resource: full admin.", label))
    if _allow(s):
        for pattern in s.actions:
            if pattern.endswith(":*") and _star_resource(s):
                found.append(Gotcha("I02", "medium", f"{label} allows every `{pattern[:-2]}` action on every "
                                    f"resource (`{pattern}` on `*`).", label))
        if _star_resource(s):
            scoped = [a for a in s.actions if "*" not in a and any(action_matches(a, r) for r in RESOURCE_SCOPED)]
            if scoped:
                found.append(Gotcha("I03", "low", f"{label} uses `Resource: \"*\"` for {', '.join(scoped)}, which "
                                    "can be limited to specific ARNs.", label))
        escalation = [a for a in ESCALATION if _grants(s, a)]
        if escalation and (_star_resource(s) or "iam:PassRole" in escalation):
            found.append(Gotcha("I04", "high", f"{label} allows privilege-escalation actions: "
                                f"{', '.join(escalation[:6])}{' …' if len(escalation) > 6 else ''}.", label))
        if _grants(s, "iam:PassRole") and (_star_resource(s) or not _has_key(s, "iam:PassedToService")):
            found.append(Gotcha("I05", "medium", f"{label} allows `iam:PassRole` "
                                f"{'on every role' if _star_resource(s) else 'without an iam:PassedToService condition'}"
                                ": a caller can hand a powerful role to a service it controls.", label))
        reads = [a for a in DATA_READS if _grants(s, a)]
        if reads and _star_resource(s):
            found.append(Gotcha("I06", "medium", f"{label} allows broad data/secret reads on every resource: "
                                f"{', '.join(reads[:5])}.", label))
        if s.not_actions:
            found.append(Gotcha("I07", "medium", f"{label} is Allow + NotAction: it grants EVERY action except "
                                f"{', '.join(s.not_actions)}, including ones AWS adds later.", label))
        if s.not_resources:
            found.append(Gotcha("I08", "low", f"{label} is Allow + NotResource: it applies to every resource "
                                "except the ones listed.", label))
        if any(p in ("*", "AWS:*") for p in s.principals) and not s.conditions:
            found.append(Gotcha("I09", "high", f"{label} allows ANY principal (`\"*\"`) with no restricting "
                                "condition: it is public.", label))
        found += _trust(s)
        found += _s3_mismatch(s)
    found += _conditions(s)
    if s.has_action_key and not (s.actions or s.not_actions) or s.has_resource_key and not (
            s.resources or s.not_resources):
        found.append(Gotcha("I16", "low", f"{label} has an empty Action or Resource list, so it does nothing.",
                            label))
    found += _unknown_services(s)
    return found


def _has_key(s: Statement, key: str) -> bool:
    return any(k.casefold() == key.casefold() for _, k, _ in s.conditions)


def _trust(s: Statement) -> list[Gotcha]:
    assume = [a for a in s.actions if a.casefold().startswith("sts:assumerole")]
    accounts = [p for p in s.principals if p.startswith("AWS:") and p != "AWS:*"]
    if assume and accounts and not _has_key(s, "sts:ExternalId"):
        return [Gotcha("I10", "medium", f"{s.label} lets another account assume this role with no `sts:ExternalId` "
                       "condition. If that account is a third party, is an external id needed?", s.label)]
    return []


def _s3_mismatch(s: Statement) -> list[Gotcha]:
    found = []
    for resource in s.resources:
        if not resource.startswith("arn:aws:s3:::"):
            continue
        is_object = "/" in resource[len("arn:aws:s3:::"):]
        if "s3:ListBucket" in s.actions and is_object:
            found.append(Gotcha("I11", "low", f"{s.label}: `s3:ListBucket` applies to the bucket ARN, not "
                                f"`{resource}`, so it grants nothing there.", resource))
        if any(a in s.actions for a in ("s3:GetObject", "s3:PutObject")) and not is_object:
            found.append(Gotcha("I11", "low", f"{s.label}: object actions need `{resource}/*`; on the bucket ARN "
                                "they grant nothing.", resource))
    return found


def _conditions(s: Statement) -> list[Gotcha]:
    found = []
    for operator, key, values in s.conditions:
        if key.casefold() == "aws:sourceip" and any(v in OPEN_CIDRS for v in values):
            found.append(Gotcha("I15", "low", f"{s.label}: `aws:SourceIp` allows `0.0.0.0/0`, i.e. any address.",
                                s.label))
        elif operator.startswith("StringLike") and values and all(v == "*" for v in values):
            found.append(Gotcha("I15", "low", f"{s.label}: `{operator}` on `{key}` with only `*` matches anything.",
                                s.label))
        elif operator.endswith("IfExists"):
            found.append(Gotcha("I15", "info", f"{s.label}: `{operator}` passes when `{key}` is missing from the "
                                "request.", s.label))
    return found


def _unknown_services(s: Statement) -> list[Gotcha]:
    unknown = sorted({a.split(":")[0] for a in (*s.actions, *s.not_actions)
                      if ":" in a and a.split(":")[0].casefold() not in SERVICE_PREFIXES})
    if not unknown:
        return []
    return [Gotcha("I18", "low", f"{s.label}: I don't know the service prefix {', '.join(unknown)}; check the "
                   "spelling (my list has about 200 prefixes).", s.label)]


def _policy_level(policy: Policy) -> list[Gotcha]:
    found = []
    uses_variables = any("${" in r for s in policy.statements for r in s.resources)
    if uses_variables and policy.version != "2012-10-17":
        found.append(Gotcha("I12", "low", "Policy variables like `${aws:username}` need `\"Version\": "
                            "\"2012-10-17\"`; without it they are read as literal text.", "Version"))
    duplicates = [sid for sid, n in Counter(s.sid for s in policy.statements if s.sid).items() if n > 1]
    for sid in duplicates:
        found.append(Gotcha("I13", "low", f"The Sid `{sid}` is used more than once.", sid))
    found += _shadowed(policy)
    full = any(_allow(s) and ("iam:*" in s.actions or "*" in s.actions) and _star_resource(s) for s in policy.statements)
    assume = any(_grants(s, "sts:AssumeRole") for s in policy.statements)
    if full and assume:
        found.append(Gotcha("I17", "high", "`iam:*` on `*` together with `sts:AssumeRole`: it can create any role "
                            "and then become it.", "iam:* + sts:AssumeRole"))
    return found


def _shadowed(policy: Policy) -> list[Gotcha]:
    """An Allow whose every action and resource is also covered by a Deny never takes effect."""
    found = []
    denies = [s for s in policy.statements if s.effect.casefold() == "deny" and not s.conditions]
    for s in policy.statements:
        if not _allow(s) or not s.actions or not s.resources:
            continue
        for deny in denies:
            if all(iam.covers_action(deny, a) for a in s.actions) and all(
                    iam.covers_resource(deny, r) for r in s.resources):
                found.append(Gotcha("I14", "low", f"{s.label} is never effective: {deny.label} denies the same "
                                    "actions on the same resources, and an explicit Deny wins.", s.label))
                break
    return found
