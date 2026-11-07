"""Pure: the per-resource waste rules (waste.py runs them and adds the stack-level W03)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import resources as rs
from waste import (
    BIG_SIZE,
    ENDPOINT_AZ_MONTH,
    ENDPOINT_FREE_SUBNETS,
    GP2_SAVING_PER_GB,
    IOPS_LIMIT,
    LAMBDA_MEMORY_MB,
    PREV_GEN,
    TAGGABLE,
    is_non_prod,
    violation,
)

R = Mapping[str, Any]
SIZE_ATTRS = {"aws_instance": "instance_type", "aws_db_instance": "instance_class",
              "aws_rds_cluster_instance": "instance_class", "aws_elasticache_cluster": "node_type",
              "aws_elasticache_replication_group": "node_type"}


def _monthly(res: R, ctx) -> float:
    return float((ctx["lines"].get(res["address"]) or {}).get("monthly") or 0.0)


def w01_gp2(res: R, ctx) -> list[dict]:
    attr = {"aws_ebs_volume": "type", "aws_db_instance": "storage_type"}.get(res["type"])
    if not attr or rs.text(res["attrs"].get(attr)) != "gp2":
        return []
    gb = rs.to_number(res["attrs"].get("size" if attr == "type" else "allocated_storage")) or 0.0
    return [violation("W01", res, attr, "gp2", gb * GP2_SAVING_PER_GB * res["qty"] or None,
                      f"{gb:g} GB moved to gp3 at the same size")]


def w02_eip(res: R, ctx) -> list[dict]:
    if res["type"] != "aws_eip" or res["attrs"].get("instance") or res["attrs"].get("network_interface"):
        return []
    if res["referenced_by"]:
        return []
    return [violation("W02", res, "type", res["source_type"], _monthly(res, ctx),
                      "nothing in this code attaches or associates it")]


def _size(res: R) -> tuple[str, str]:
    attr = SIZE_ATTRS.get(res["type"], "")
    return attr, rs.text(res["attrs"].get(attr)) if attr else ""


def w04_prev_gen(res: R, ctx) -> list[dict]:
    attr, size = _size(res)
    family = size.removeprefix("db.").removeprefix("cache.").split(".")[0]
    if not size or family not in PREV_GEN:
        return []
    return [violation("W04", res, attr, size, None, "newer generations are usually cheaper per unit; verify")]


def w05_big(res: R, ctx) -> list[dict]:
    attr, size = _size(res)
    match = BIG_SIZE.search(size)
    if not match or (match.group(1) and int(match.group(1)) < 2) or not is_non_prod(res, ctx):
        return []
    return [violation("W05", res, attr, size, _monthly(res, ctx) * 0.5 or None, "assumes one size down")]


def w06_multi_az(res: R, ctx) -> list[dict]:
    if res["type"] != "aws_db_instance" or not rs.to_bool(res["attrs"].get("multi_az")) or not is_non_prod(res, ctx):
        return []
    return [violation("W06", res, "multi_az", res["attrs"]["multi_az"], _monthly(res, ctx) * 0.5 or None,
                      "assumes single-AZ is acceptable outside prod")]


def w07_iops(res: R, ctx) -> list[dict]:
    kind_attr = {"aws_ebs_volume": "type", "aws_db_instance": "storage_type"}.get(res["type"])
    iops = rs.to_number(res["attrs"].get("iops")) or 0.0
    if not kind_attr or rs.text(res["attrs"].get(kind_attr)) not in ("io1", "io2") or iops <= IOPS_LIMIT:
        return []
    return [violation("W07", res, "iops", iops, None, "check the workload needs this")] if is_non_prod(res, ctx) else []


def w08_s3(res: R, ctx) -> list[dict]:
    if res["type"] != "aws_s3_bucket" or res["attrs"].get("lifecycle_rule"):
        return []
    if "aws_s3_bucket_lifecycle_configuration" in res["referenced_by"]:
        return []
    return [violation("W08", res, "type", res["source_type"], None, "objects are kept until deleted")]


def w09_logs(res: R, ctx) -> list[dict]:
    if res["type"] != "aws_cloudwatch_log_group" or rs.to_number(res["attrs"].get("retention_in_days")):
        return []
    return [violation("W09", res, "type", res["source_type"], None, "retention never expires; storage grows")]


def w10_dynamo(res: R, ctx) -> list[dict]:
    mode = (rs.text(res["attrs"].get("billing_mode")) or "PROVISIONED").upper()
    if res["type"] != "aws_dynamodb_table" or mode != "PROVISIONED" or not is_non_prod(res, ctx):
        return []
    if "aws_appautoscaling_target" in res["referenced_by"]:
        return []
    return [violation("W10", res, "type", res["source_type"], None, "capacity is paid whether used or not")]


def w11_volume(res: R, ctx) -> list[dict]:
    if res["type"] != "aws_ebs_volume" or {"aws_volume_attachment", "aws_instance"} & set(res["referenced_by"]):
        return []
    return [violation("W11", res, "type", res["source_type"], _monthly(res, ctx) or None,
                      "nothing in this code attaches it")]


def w12_tags(res: R, ctx) -> list[dict]:
    line = ctx["lines"].get(res["address"]) or {}
    if res["type"] not in TAGGABLE or line.get("basis") != "priced" or not res["tags_known"]:
        return []
    have = {k.casefold() for k in (*res["tags"], *ctx["default_tags"])}
    missing = [group[0] for group in ctx["tag_groups"] if not have & {g.casefold() for g in group}]
    if not missing:
        return []
    return [violation("W12", res, "tags", res["source_type"], None, "provider default_tags counted",
                      title=f"Missing cost-allocation tags: {', '.join(missing)}")]


def w13_endpoint(res: R, ctx) -> list[dict]:
    subnets = res["attrs"].get("subnet_ids")
    kind = (rs.text(res["attrs"].get("vpc_endpoint_type")) or "").casefold()
    if res["type"] != "aws_vpc_endpoint" or kind != "interface" or not isinstance(subnets, list):
        return []
    if len(subnets) <= ENDPOINT_FREE_SUBNETS or not is_non_prod(res, ctx):
        return []
    return [violation("W13", res, "type", res["source_type"],
                      (len(subnets) - ENDPOINT_FREE_SUBNETS) * ENDPOINT_AZ_MONTH * res["qty"],
                      f"{len(subnets)} subnets; 2 assumed enough outside prod")]


def w14_lambda(res: R, ctx) -> list[dict]:
    memory = rs.to_number(res["attrs"].get("memory_size")) or 0.0
    if res["type"] != "aws_lambda_function" or res["attrs"].get("architectures") or memory < LAMBDA_MEMORY_MB:
        return []
    return [violation("W14", res, "memory_size", memory, None, "x86 by default; arm64 needs compatible code")]


RULE_CHECKS = (w01_gp2, w02_eip, w04_prev_gen, w05_big, w06_multi_az, w07_iops, w08_s3, w09_logs, w10_dynamo,
               w11_volume, w12_tags, w13_endpoint, w14_lambda)
