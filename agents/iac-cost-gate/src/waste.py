"""Pure: the waste rules W01-W14 over the parsed resources and their cost lines. Rule wording is this
agent's own (the rule categories follow the usual FinOps checks; no third-party rule text is copied).

Each violation: {rule_id, severity, title, address, attribute, evidence (verbatim value from the input),
est_monthly_saving (or None), assumption, hint}. Savings are per rule and never summed into a promise."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import prices as p
import resources as rs

NON_PROD = frozenset({"dev", "development", "test", "testing", "staging", "stage", "sandbox", "qa", "nonprod",
                      "non-prod", "preprod", "uat"})
ENV_KEYS = ("environment", "env", "stage")
DEFAULT_TAG_GROUPS = (("team", "owner"), ("environment", "env"), ("project", "service"))
PREV_GEN = ("t2", "m3", "m4", "c3", "c4", "r3", "r4", "i2", "d2")
TAGGABLE = frozenset({"aws_instance", "aws_db_instance", "aws_rds_cluster_instance", "aws_ebs_volume", "aws_nat_gateway",
                      "aws_lb", "aws_alb", "aws_eks_cluster", "aws_eip", "aws_elasticache_cluster",
                      "aws_elasticache_replication_group", "aws_vpc_endpoint", "aws_dynamodb_table"})
BIG_SIZE = re.compile(r"\.(?:(\d+)xlarge|metal)$")
GP2_SAVING_PER_GB = 0.02
IOPS_LIMIT = 10_000
ENDPOINT_FREE_SUBNETS = 2
LAMBDA_MEMORY_MB = 1024
NAT_HOURLY_MONTH = round(p.NAT_HOURLY * p.HOURS_PER_MONTH, 2)
ENDPOINT_AZ_MONTH = round(p.ENDPOINT_HOURLY_PER_AZ * p.HOURS_PER_MONTH, 2)

RULES = {  # id: (severity, title, hint)
    "W01": ("low", "gp2 storage (gp3 is cheaper per GB)", "Switch the storage type to gp3."),
    "W02": ("medium", "Elastic IP not attached to anything", "Release the address or attach it to a resource."),
    "W03": ("medium", "More than one NAT gateway outside prod", "Share one NAT gateway outside prod."),
    "W04": ("low", "Previous-generation instance family", "Move to a current generation; verify the size."),
    "W05": ("medium", "2xlarge or larger outside prod", "Try one size down outside prod."),
    "W06": ("medium", "Multi-AZ database outside prod", "Use single-AZ outside prod if that is acceptable."),
    "W07": ("low", "Provisioned IOPS above 10,000 outside prod", "Lower the IOPS or use gp3 outside prod."),
    "W08": ("low", "S3 bucket without a lifecycle rule", "Add a lifecycle rule to expire or tier old objects."),
    "W09": ("medium", "Log group without retention (logs never expire)", "Set retention_in_days."),
    "W10": ("low", "Provisioned DynamoDB capacity without autoscaling outside prod",
            "Add an autoscaling target or switch to on-demand."),
    "W11": ("medium", "EBS volume not attached to any instance", "Attach the volume or delete it."),
    "W12": ("low", "Missing cost-allocation tags", "Add the missing tags (or set provider default_tags)."),
    "W13": ("low", "Interface endpoint in more than 2 subnets outside prod", "Use at most 2 subnets outside prod."),
    "W14": ("low", "x86 Lambda with 1024 MB or more", "Try arm64 (Graviton is often ~20% cheaper per ms; verify)."),
}


def value_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def env_of(res: Mapping[str, Any], default_tags: Mapping[str, str], fallback: str) -> str:
    for tags in (res["tags"], default_tags):
        lowered = {k.casefold(): str(v).casefold() for k, v in tags.items()}
        for key in ENV_KEYS:
            if lowered.get(key) and rs.UNRESOLVED not in lowered[key]:
                return lowered[key]
    return fallback.casefold()


def violation(rule_id: str, res: Mapping[str, Any], attribute: str, evidence: Any, saving: float | None,
              assumption: str = "", title: str = "") -> dict:
    severity, default_title, hint = RULES[rule_id]
    return {"rule_id": rule_id, "severity": severity, "title": title or default_title, "address": res["address"],
            "attribute": attribute, "evidence": value_text(evidence),
            "est_monthly_saving": None if saving is None else round(saving, 2), "assumption": assumption,
            "hint": hint}


def check(resources: Sequence[Mapping[str, Any]], lines: Mapping[str, Mapping[str, Any]],
          default_tags: Mapping[str, str], env: str = "", tag_groups: Sequence[Sequence[str]] = DEFAULT_TAG_GROUPS
          ) -> list[dict]:
    from waste_rules import RULE_CHECKS

    out: list[dict] = []
    ctx = {"lines": lines, "default_tags": default_tags, "env": env, "tag_groups": tag_groups,
           "resources": resources}
    for res in resources:
        for rule in RULE_CHECKS:
            out += rule(res, ctx)
    nats = [r for r in resources if r["type"] == "aws_nat_gateway" and is_non_prod(r, ctx)]
    total = sum(r["qty"] for r in nats)
    if total > 1:
        out.append(violation("W03", nats[-1], "type", nats[-1]["source_type"], (total - 1) * NAT_HOURLY_MONTH,
                             f"{total} NAT gateways outside prod; one shared gateway assumed enough"))
    return out


def is_non_prod(res: Mapping[str, Any], ctx: Mapping[str, Any]) -> bool:
    return env_of(res, ctx["default_tags"], ctx["env"]) in NON_PROD
