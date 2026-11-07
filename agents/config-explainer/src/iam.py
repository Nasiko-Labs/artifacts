"""Pure: reading an AWS IAM policy (identity, resource or trust policy, or a single statement) into a
statement table, ARN parsing, wildcard matching and the "can it do X to Y?" answer. Conditions are listed,
never evaluated. No AWS dependency."""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass

_ARN = re.compile(r"^arn:([^:]*):([^:]*):([^:]*):([^:]*):(.*)$")


class PolicyError(ValueError):
    """Not a policy this reader accepts."""


@dataclass(frozen=True)
class Statement:
    index: int                  # 1-based
    sid: str
    effect: str
    actions: tuple[str, ...]
    not_actions: tuple[str, ...]
    resources: tuple[str, ...]
    not_resources: tuple[str, ...]
    principals: tuple[str, ...]
    not_principals: tuple[str, ...]
    conditions: tuple[tuple[str, str, tuple[str, ...]], ...]
    has_action_key: bool = True
    has_resource_key: bool = True

    @property
    def label(self) -> str:
        return f"Statement {self.index}" + (f" ({self.sid})" if self.sid else "")


@dataclass(frozen=True)
class Policy:
    version: str | None
    statements: tuple[Statement, ...]


def _list(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(str(v) for v in value)
    raise PolicyError(f"expected a string or a list, got {type(value).__name__}")


def _principals(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        return tuple(f"{kind}:{p}" for kind, items in value.items() for p in _list(items))
    raise PolicyError("Principal must be \"*\" or an object")


def _conditions(value: object) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    if not isinstance(value, dict):
        return ()
    out = []
    for operator, block in value.items():
        if isinstance(block, dict):
            out += [(str(operator), str(key), _list(values)) for key, values in block.items()]
    return tuple(out)


def parse(text: str) -> Policy:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise PolicyError(f"not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise PolicyError("a policy is a JSON object")
    raw = data.get("Statement", data if "Effect" in data else None)
    if raw is None:
        raise PolicyError("no Statement found")
    items = raw if isinstance(raw, list) else [raw]
    statements = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise PolicyError(f"statement {index} is not an object")
        statements.append(Statement(
            index=index, sid=str(item.get("Sid") or ""), effect=str(item.get("Effect") or ""),
            actions=_list(item.get("Action")), not_actions=_list(item.get("NotAction")),
            resources=_list(item.get("Resource")), not_resources=_list(item.get("NotResource")),
            principals=_principals(item.get("Principal")), not_principals=_principals(item.get("NotPrincipal")),
            conditions=_conditions(item.get("Condition")),
            has_action_key="Action" in item or "NotAction" in item,
            has_resource_key="Resource" in item or "NotResource" in item))
    version = data.get("Version") if "Statement" in data else None
    return Policy(version=str(version) if version else None, statements=tuple(statements))


def parse_arn(arn: str) -> dict:
    match = _ARN.match(arn)
    if not match:
        return {"arn": arn, "wildcard": "*" in arn}
    _, service, region, account, resource = match.groups()
    return {"arn": arn, "service": service, "region": region or "any", "account": account or "any",
            "resource": resource, "wildcard": "*" in arn or "?" in arn}


def action_matches(pattern: str, action: str) -> bool:
    return fnmatch.fnmatchcase(action.casefold(), pattern.casefold())


def resource_matches(pattern: str, resource: str) -> bool:
    return fnmatch.fnmatchcase(resource, pattern.replace("[", "[[]"))


def covers_action(statement: Statement, action: str) -> bool:
    if statement.not_actions:
        return not any(action_matches(p, action) for p in statement.not_actions)
    return any(action_matches(p, action) for p in statement.actions)


def covers_resource(statement: Statement, resource: str | None) -> bool:
    if resource is None:
        return True
    if statement.not_resources:
        return not any(resource_matches(p, resource) for p in statement.not_resources)
    if not statement.has_resource_key and statement.principals:
        return True  # resource policy: the resource is the one it's attached to
    return any(resource_matches(p, resource) for p in statement.resources)


def evaluate(policy: Policy, action: str, resource: str | None) -> str:
    """The answer to "can it do `action` on `resource`?", by statement. Explicit Deny wins."""
    target = f"`{action}`" + (f" on `{resource}`" if resource else "")
    hits = [s for s in policy.statements if covers_action(s, action) and covers_resource(s, resource)]
    denies = [s for s in hits if s.effect.casefold() == "deny"]
    allows = [s for s in hits if s.effect.casefold() == "allow"]
    if denies:
        return f"{target}: **denied** by {denies[0].label} (an explicit Deny wins over any Allow)."
    if allows:
        conditional = [s for s in allows if s.conditions]
        text = f"{target}: **allowed** by {', '.join(s.label for s in allows)}"
        if conditional and len(conditional) == len(allows):
            text += " only if its conditions hold (I list conditions but don't evaluate them)"
        return text + "."
    return f"{target}: **not allowed** (no matching Allow, so the implicit deny applies)."


def statement_facts(statement: Statement) -> dict:
    return {"statement": statement.label, "effect": statement.effect, "actions": list(statement.actions),
            "not_actions": list(statement.not_actions), "resources": [parse_arn(r) for r in statement.resources],
            "not_resources": list(statement.not_resources), "principals": list(statement.principals),
            "not_principals": list(statement.not_principals),
            "conditions": [{"operator": o, "key": k, "values": list(v)} for o, k, v in statement.conditions]}
