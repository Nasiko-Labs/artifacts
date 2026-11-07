"""Pure: one cost line per resource from the static table (prices.py). Hourly x 730, then the region
multiplier. Unknown sizes and unknown types are `unpriced`; usage-billed services are `usage_dependent`
($0, never estimated); wiring resources are `free`. Every guessed default is an assumption on its line."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import prices as p
import resources as rs

H = p.HOURS_PER_MONTH
USAGE = "usage"


def line(res: Mapping[str, Any], mult: float, nat_gb: float = p.NAT_DEFAULT_GB) -> dict:
    rtype, attrs = res["type"], res["attrs"]
    pricer = _PRICERS.get(rtype)
    if pricer is not None:
        monthly, detail, assumptions = pricer(attrs, nat_gb)
        if monthly is None:
            return _line(res, 0.0, "unpriced", detail, assumptions)
        if monthly == USAGE:
            return _line(res, 0.0, "usage_dependent", detail, assumptions)
        return _line(res, round(monthly * mult * res["qty"], 2), "priced", detail, assumptions)
    if rtype in p.USAGE_PRICED:
        return _line(res, 0.0, "usage_dependent", "", ["priced by usage, not estimated"])
    if p.is_free(rtype):
        return _line(res, 0.0, "free", "", [])
    reason = "module not fetched" if rtype == "module" else "not in my price table"
    return _line(res, 0.0, "unpriced", "", [reason])


def _line(res, monthly, basis, detail, assumptions) -> dict:
    return {"address": res["address"], "type": res["type"], "qty": res["qty"], "monthly": monthly, "basis": basis,
            "detail": detail, "assumptions": list(assumptions), "change": res.get("change") or ""}


def _sized(table: dict, size: str, what: str):
    if not size:
        return None, "", [f"{what} not set or not resolvable"]
    if size not in table:
        return None, size, [f"{size} not in my price table"]
    return table[size] * H, size, []


def _ec2(a, _):
    return _sized(p.EC2_HOURLY, rs.text(a.get("instance_type")), "instance_type")


def _rds(a, _, factor: float = 1.0):
    monthly, size, notes = _sized(p.RDS_HOURLY, rs.text(a.get("instance_class")), "instance_class")
    if monthly is None:
        return None, size, notes
    multi = rs.to_bool(a.get("multi_az"))
    copies = p.MULTI_AZ_FACTOR if multi else 1
    monthly *= factor * copies
    gb = rs.to_number(a.get("allocated_storage")) or 0.0
    kind = rs.text(a.get("storage_type")) or ("io1" if rs.to_number(a.get("iops")) else "gp2")
    if not rs.text(a.get("storage_type")) and gb:
        notes.append(f"storage type not set: assumed {kind}")
    monthly += gb * p.RDS_STORAGE_GB.get(kind, p.RDS_STORAGE_GB["gp2"]) * copies
    if kind in ("io1", "io2"):
        monthly += (rs.to_number(a.get("iops")) or 0.0) * p.RDS_IOPS_MONTH * copies
    return monthly, f"{size}{', multi-AZ' if multi else ''}{f', {gb:g} GB {kind}' if gb else ''}", notes


def _aurora(a, nat_gb):
    monthly, detail, notes = _rds(a, nat_gb, p.AURORA_FACTOR)
    return monthly, detail, [*notes, "Aurora priced as the instance row x1.2"]


def _ebs(a, _):
    gb = rs.to_number(a.get("size"))
    kind = rs.text(a.get("type"))
    notes = [] if kind else ["volume type not set: assumed gp2"]
    kind = kind or "gp2"
    if gb is None or kind not in p.EBS_GB:
        return None, kind, notes + ["size or type not priceable"]
    monthly = gb * p.EBS_GB[kind]
    iops, mbps = rs.to_number(a.get("iops")) or 0.0, rs.to_number(a.get("throughput")) or 0.0
    if kind in ("io1", "io2"):
        monthly += iops * p.EBS_PIOPS_MONTH
    if kind == "gp3":
        monthly += max(iops - p.GP3_FREE_IOPS, 0) * p.GP3_IOPS_MONTH + max(mbps - p.GP3_FREE_MBPS, 0) * p.GP3_MBPS_MONTH
    return monthly, f"{gb:g} GB {kind}", notes


def _nat(_, nat_gb):
    return p.NAT_HOURLY * H + nat_gb * p.NAT_PER_GB, "", [f"NAT data {nat_gb:g} GB/month assumed"]


def _lb(a, _):
    kind = rs.text(a.get("load_balancer_type")) or "application"
    if kind not in ("application", "network"):
        return None, kind, [f"{kind} load balancer not in my price table"]
    return (p.LB_HOURLY + p.LCU_HOURLY * p.LB_DEFAULT_LCU) * H, kind, [f"{p.LB_DEFAULT_LCU} LCU assumed"]


def _cache(a, _):
    monthly, size, notes = _sized(p.CACHE_HOURLY, rs.text(a.get("node_type")), "node_type")
    nodes = rs.to_number(a.get("num_cache_nodes") or a.get("num_cache_clusters")) or 1
    return (None if monthly is None else monthly * nodes), f"{size} x{nodes:g}" if size else "", notes


def _endpoint(a, _):
    kind = (rs.text(a.get("vpc_endpoint_type")) or "Gateway").casefold()
    if kind != "interface":
        return 0.0, kind, []
    subnets = a.get("subnet_ids")
    azs = len(subnets) if isinstance(subnets, list) else 1
    notes = [] if isinstance(subnets, list) else ["subnets not resolvable: assumed 1 AZ"]
    monthly = p.ENDPOINT_HOURLY_PER_AZ * H * azs + p.ENDPOINT_DEFAULT_GB * p.ENDPOINT_PER_GB
    return monthly, f"interface, {azs} AZ", [*notes, f"{p.ENDPOINT_DEFAULT_GB:g} GB/month through the endpoint assumed"]


def _dynamo(a, _):
    mode = (rs.text(a.get("billing_mode")) or "PROVISIONED").upper()
    if mode != "PROVISIONED":
        return USAGE, "on-demand", ["on-demand: priced by usage, not estimated"]
    wcu, rcu = rs.to_number(a.get("write_capacity")) or 0.0, rs.to_number(a.get("read_capacity")) or 0.0
    return (wcu * p.DYNAMO_WCU_HOURLY + rcu * p.DYNAMO_RCU_HOURLY) * H, f"{wcu:g} WCU / {rcu:g} RCU", []


_PRICERS = {
    "aws_instance": _ec2, "aws_db_instance": _rds, "aws_rds_cluster_instance": _aurora, "aws_ebs_volume": _ebs,
    "aws_nat_gateway": _nat, "aws_lb": _lb, "aws_alb": _lb,
    "aws_eks_cluster": lambda a, _: (p.EKS_HOURLY * H, "control plane", []),
    "aws_eip": lambda a, _: (p.EIP_HOURLY * H, "", []),
    "aws_elasticache_cluster": _cache, "aws_elasticache_replication_group": _cache,
    "aws_vpc_endpoint": _endpoint, "aws_dynamodb_table": _dynamo,
}
