"""Pure: read a pasted resource inventory into `Resource` records, in code (never by the model).

Two shapes, which may be pasted together in one message:
  1. CSV (comma, tab or semicolon separated) under a header row; columns are matched by synonyms and
     `tag_<key>` columns become tags.
  2. JSON from the standard describe/list commands (volumes, instances, snapshots, addresses, load
     balancers, NAT gateways), including `Tags: [{Key, Value}]`, or a JSON array of flat rows that use
     the CSV column names.
A row without an id, with the wrong number of cells, or of a type that cannot be recognised is counted
as unreadable, never guessed.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_ROWS = 500
KINDS = ("volume", "instance", "snapshot", "address", "load_balancer", "nat_gateway")

# CSV header synonyms (normalised: lower case, spaces and dashes as underscores). Order = priority.
ID_COLUMNS = ("resource_id", "id", "snapshot_id", "snapshotid", "instance_id", "instanceid", "volume_id", "volumeid",
              "allocation_id", "allocationid", "nat_gateway_id", "load_balancer_name", "arn", "resource")
SYNONYMS: dict[str, tuple[str, ...]] = {
    "type": ("type", "resource_type", "resourcetype", "kind", "service"),
    "name": ("name", "resource_name"),
    "region": ("region", "availability_zone", "az", "location"),
    "state": ("state", "status"),
    "created": ("created", "create_time", "createtime", "created_at", "creation_date", "launch_time", "launchtime",
                "start_time", "starttime", "allocated_at"),
    "last_activity": ("last_activity", "last_used", "last_attached", "last_activity_date", "last_attach_time"),
    "state_changed": ("state_changed", "stopped_since", "stopped_at", "state_change_time", "detached_at"),
    "size_gb": ("size_gb", "size", "volume_size", "size_gib", "storage_gb", "attached_storage_gb"),
    "volume_type": ("volume_type", "volumetype", "storage_type"),
    "attached_to": ("attached_to", "attachments", "attachment", "attached_instance", "association",
                    "association_id", "associated_with", "instance"),
    "cpu_avg": ("cpu_avg", "cpu_avg_14d", "avg_cpu", "cpu", "cpu_utilization"),
    "network_bytes_per_day": ("network_bytes_per_day", "net_bytes_per_day"),
    "network_bytes_14d": ("network_bytes_14d", "network_bytes", "bytes_processed_14d", "bytes_14d"),
    "request_count_14d": ("request_count_14d", "requests_14d", "request_count"),
    "healthy_targets": ("healthy_targets", "healthy_target_count", "healthy_hosts"),
    "monthly_cost": ("monthly_cost", "cost_monthly", "monthly_cost_usd", "cost"),
    "currency": ("currency",),
    "instance_type": ("instance_type", "instancetype"),
    "source_volume_id": ("source_volume_id", "source_volume"),
    "source_exists": ("source_exists", "source_volume_exists"),
    "referenced_by_ami": ("referenced_by_ami", "in_use_by_ami", "ami_reference"),
    "tags": ("tags",),
}
PLAIN_TAG_COLUMNS = ("owner", "team", "created_by", "contact", "project", "keep", "do_not_delete", "retain")
_TYPE_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("nat_gateway", ("nat",)),
    ("load_balancer", ("load balancer", "loadbalancer", "elb", "alb", "nlb", "lb")),
    ("snapshot", ("snapshot", "snap")),
    ("address", ("elastic ip", "eip", "address", "ip", "public ip")),
    ("volume", ("volume", "ebs", "disk")),
    ("instance", ("instance", "ec2", "vm", "server")),
)
_ID_PREFIXES = (("vol-", "volume"), ("i-", "instance"), ("snap-", "snapshot"), ("eipalloc-", "address"),
                ("nat-", "nat_gateway"))
_TRUE = frozenset({"true", "yes", "y", "1", "t"})
_FALSE = frozenset({"false", "no", "n", "0", "f"})
_EMPTY = frozenset({"", "-", "none", "null", "n/a", "na", "unknown", "[]", "{}"})
_UNIT = re.compile(r"(?i)\s*(gib|gb|%|usd)\s*$")
_DELIMITERS = (",", "\t", ";")
_STATE_TIME = re.compile(r"\((\d{4}-\d{2}-\d{2})[ T]")
_AMI = re.compile(r"\bami-[0-9a-f]+", re.IGNORECASE)


@dataclass(frozen=True)
class Resource:
    id: str
    kind: str
    name: str = ""
    region: str = ""
    state: str = ""
    created: date | None = None
    last_activity: date | None = None
    state_changed: date | None = None
    size_gb: float | None = None
    volume_type: str = ""
    attached_to: str = ""
    attached: bool | None = None
    cpu_avg: float | None = None
    network_bytes_per_day: float | None = None
    network_bytes_14d: float | None = None
    request_count_14d: float | None = None
    healthy_targets: float | None = None
    monthly_cost: Decimal | None = None
    currency: str = ""
    instance_type: str = ""
    source_volume_id: str = ""
    source_exists: bool | None = None
    referenced_by_ami: bool | None = None
    volume_ids: tuple[str, ...] = ()
    tags: tuple[tuple[str, str], ...] = ()

    def tag_values(self) -> dict[str, str]:
        """Tag key (case-folded) → value; the first occurrence of a key wins."""
        found: dict[str, str] = {}
        for key, value in self.tags:
            found.setdefault(key.strip().casefold(), value.strip())
        return found


@dataclass(frozen=True)
class Parsed:
    resources: tuple[Resource, ...]
    unreadable: int = 0      # rows counted but not read (no id, bad cells, unknown type)
    total_rows: int = 0      # every row seen, read or not
    over_limit: int = 0      # rows past MAX_ROWS, not read


def find_inventory(text: str) -> Parsed | None:
    """The inventory in a message, or None when the message holds no recognisable paste."""
    json_rows, json_bad, rest = _json_resources(text or "")
    csv_parsed = _csv_resources(rest)
    resources = (*json_rows, *(csv_parsed.resources if csv_parsed else ()))
    bad = json_bad + (csv_parsed.unreadable if csv_parsed else 0)
    if not resources and not bad and csv_parsed is None:
        return None
    unique = _dedupe(resources)
    total = len(unique) + bad
    kept, over = unique[:MAX_ROWS], max(0, len(unique) - MAX_ROWS)
    return Parsed(_with_cross_refs(kept), bad, total, over)


# --- CSV -----------------------------------------------------------------------------------------

def _norm_header(cell: str) -> str:
    return re.sub(r"[\s\-./]+", "_", cell.strip().strip('"').casefold()).strip("_")


def _header_at(line: str) -> tuple[str, list[str]] | None:
    for delimiter in _DELIMITERS:
        if delimiter not in line:
            continue
        cells = [_norm_header(c) for c in next(csv.reader([line], delimiter=delimiter))]
        if len(cells) >= 2 and any(c in ID_COLUMNS for c in cells):
            return delimiter, cells
    return None


def _csv_resources(text: str) -> Parsed | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        header = _header_at(line)
        if header is None:
            continue
        delimiter, cells = header
        body = []
        for row_line in lines[index + 1:]:
            if not row_line.strip():
                if body:
                    break
                continue
            if delimiter not in row_line and len(cells) > 1:
                break
            body.append(row_line)
        rows = list(csv.reader(io.StringIO("\n".join(body)), delimiter=delimiter))
        return _rows_to_resources(cells, rows)
    return None


def _column_map(cells: Sequence[str]) -> dict[str, int]:
    """Field name → column index, by synonym priority; the id column first."""
    taken: dict[str, int] = {}
    id_col = next((cells.index(c) for c in ID_COLUMNS if c in cells), None)
    if id_col is not None:
        taken["id"] = id_col
    for field_name, names in SYNONYMS.items():
        col = next((cells.index(n) for n in names if n in cells and cells.index(n) not in taken.values()), None)
        if col is not None:
            taken[field_name] = col
    if "source_volume_id" not in taken:  # a snapshot sheet: "volume_id" beside "snapshot_id" is the source
        spare = next((cells.index(c) for c in ("volume_id", "volumeid") if c in cells
                      and cells.index(c) not in taken.values()), None)
        if spare is not None:
            taken["source_volume_id"] = spare
    return taken


def _rows_to_resources(cells: Sequence[str], rows: Iterable[Sequence[str]]) -> Parsed:
    columns = _column_map(cells)
    tag_columns = {i: c[4:] for i, c in enumerate(cells) if c.startswith("tag_") and len(c) > 4}
    tag_columns.update({i: c for i, c in enumerate(cells) if c in PLAIN_TAG_COLUMNS and i not in columns.values()})
    resources: tuple[Resource, ...] = ()
    bad = 0
    for row in rows:
        if len(row) != len(cells):
            bad += 1
            continue
        record = {name: row[col].strip() for name, col in columns.items()}
        tags = tuple((key, row[col].strip()) for col, key in tag_columns.items() if row[col].strip())
        resource = from_flat(record, tags)
        if resource is None:
            bad += 1
        else:
            resources = (*resources, resource)
    return Parsed(resources, bad, len(resources) + bad)


def from_flat(record: Mapping[str, Any], extra_tags: Sequence[tuple[str, str]] = ()) -> Resource | None:
    """One flat row (CSV cells or a JSON object with CSV column names) → a Resource, or None."""
    rid = _text(record.get("id"))
    if not rid:
        return None
    kind = kind_of(_text(record.get("type")), rid)
    if kind is None:
        return None
    attached_raw = record.get("attached_to")
    attached_to = "" if _is_empty(attached_raw) else _text(attached_raw)
    tags = (*extra_tags, *_tag_pairs(record.get("tags")))
    return Resource(
        id=rid, kind=kind, name=_text(record.get("name")), region=_text(record.get("region")),
        state=_text(record.get("state")).casefold(), created=to_date(record.get("created")),
        last_activity=to_date(record.get("last_activity")), state_changed=to_date(record.get("state_changed")),
        size_gb=to_number(record.get("size_gb")), volume_type=_text(record.get("volume_type")).casefold(),
        attached_to=attached_to, attached=None if "attached_to" not in record else bool(attached_to),
        cpu_avg=to_number(record.get("cpu_avg")), network_bytes_per_day=to_number(record.get("network_bytes_per_day")),
        network_bytes_14d=to_number(record.get("network_bytes_14d")),
        request_count_14d=to_number(record.get("request_count_14d")),
        healthy_targets=to_number(record.get("healthy_targets")), monthly_cost=to_money(record.get("monthly_cost")),
        currency=_text(record.get("currency")).upper(), instance_type=_text(record.get("instance_type")).casefold(),
        source_volume_id=_text(record.get("source_volume_id")), source_exists=to_bool(record.get("source_exists")),
        referenced_by_ami=to_bool(record.get("referenced_by_ami")), tags=tuple(tags))


def kind_of(type_text: str, rid: str) -> str | None:
    lowered = type_text.casefold().replace("::", " ").replace("_", " ").replace("-", " ")
    if lowered:
        words = set(lowered.split())
        for kind, names in _TYPE_WORDS:
            if any((" " in n and n in lowered) or n in words for n in names):
                return kind
        if lowered.replace(" ", "_") in KINDS:
            return lowered.replace(" ", "_")
    lowered_id = rid.casefold()
    if ":loadbalancer/" in lowered_id:
        return "load_balancer"
    return next((kind for prefix, kind in _ID_PREFIXES if lowered_id.startswith(prefix)), None)


# --- JSON ----------------------------------------------------------------------------------------

def _json_values(text: str) -> tuple[list[Any], str]:
    """Every top-level JSON object/array in the text, and the text with them removed."""
    decoder = json.JSONDecoder()
    values: list[Any] = []
    rest: list[str] = []
    i, start = 0, 0
    while i < len(text):
        if text[i] in "{[":
            try:
                value, end = decoder.raw_decode(text, i)
            except ValueError:
                i += 1
                continue
            if isinstance(value, (dict, list)) and value:
                values.append(value)
                rest.append(text[start:i])
                i = start = end
                continue
        i += 1
    rest.append(text[start:])
    return values, "".join(rest)


def _json_resources(text: str) -> tuple[tuple[Resource, ...], int, str]:
    values, rest = _json_values(text)
    resources: tuple[Resource, ...] = ()
    bad = 0
    for value in values:
        found, unread = _from_json(value)
        resources, bad = (*resources, *found), bad + unread
    return resources, bad, rest


_DESCRIBE_KEYS = ("Volumes", "Reservations", "Instances", "Snapshots", "Addresses", "LoadBalancers",
                  "LoadBalancerDescriptions", "NatGateways")


def _from_json(value: Any) -> tuple[tuple[Resource, ...], int]:
    if isinstance(value, list):
        if not any(isinstance(v, Mapping) for v in value):
            return (), 0
        rows = [_flat_json_row(v) for v in value if isinstance(v, Mapping)]
        bad = len(value) - len(rows) + sum(1 for r in rows if r is None)
        return tuple(r for r in rows if r is not None), bad
    if not isinstance(value, Mapping):
        return (), 0
    if not any(k in value for k in _DESCRIBE_KEYS):
        row = _flat_json_row(value)
        return ((row,), 0) if row is not None else ((), 0)
    parsed: tuple[Resource | None, ...] = ()
    for item in value.get("Volumes") or ():
        parsed = (*parsed, _volume(item))
    instances = [i for r in value.get("Reservations") or () if isinstance(r, Mapping) for i in r.get("Instances") or ()]
    for item in (*instances, *(value.get("Instances") or ())):
        parsed = (*parsed, _instance(item))
    for item in value.get("Snapshots") or ():
        parsed = (*parsed, _snapshot(item))
    for item in value.get("Addresses") or ():
        parsed = (*parsed, _address(item))
    for item in (*(value.get("LoadBalancers") or ()), *(value.get("LoadBalancerDescriptions") or ())):
        parsed = (*parsed, _load_balancer(item))
    for item in value.get("NatGateways") or ():
        parsed = (*parsed, _nat(item))
    return tuple(r for r in parsed if r is not None), sum(1 for r in parsed if r is None)


def _flat_json_row(item: Mapping[str, Any]) -> Resource | None:
    cells = {_norm_header(str(k)): v for k, v in item.items()}
    columns = _column_map(list(cells))
    names = list(cells)
    record = {name: cells[names[col]] for name, col in columns.items()}
    tags = tuple((k[4:], _text(v)) for k, v in cells.items() if k.startswith("tag_") and _text(v))
    tags = (*tags, *((k, _text(v)) for k, v in cells.items() if k in PLAIN_TAG_COLUMNS and _text(v)))
    return from_flat(record, tags) if record.get("id") else None


def _aws_tags(item: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    return _tag_pairs(item.get("Tags"))


def _name_tag(tags: Sequence[tuple[str, str]]) -> str:
    return next((v for k, v in tags if k.casefold() == "name"), "")


def _volume(item: Any) -> Resource | None:
    if not isinstance(item, Mapping) or not item.get("VolumeId"):
        return None
    attachments = [a for a in item.get("Attachments") or () if isinstance(a, Mapping)]
    tags = _aws_tags(item)
    return Resource(id=str(item["VolumeId"]), kind="volume", name=_name_tag(tags),
                    region=_text(item.get("AvailabilityZone")), state=_text(item.get("State")).casefold(),
                    created=to_date(item.get("CreateTime")), size_gb=to_number(item.get("Size")),
                    volume_type=_text(item.get("VolumeType")).casefold(),
                    attached_to=", ".join(_text(a.get("InstanceId")) for a in attachments if a.get("InstanceId")),
                    attached=bool(attachments), tags=tags)


def _instance(item: Any) -> Resource | None:
    if not isinstance(item, Mapping) or not item.get("InstanceId"):
        return None
    state = item.get("State")
    reason = _STATE_TIME.search(_text(item.get("StateTransitionReason")))
    mappings = [m.get("Ebs") for m in item.get("BlockDeviceMappings") or () if isinstance(m, Mapping)]
    tags = _aws_tags(item)
    placement = item.get("Placement") if isinstance(item.get("Placement"), Mapping) else {}
    return Resource(id=str(item["InstanceId"]), kind="instance", name=_name_tag(tags),
                    region=_text(placement.get("AvailabilityZone")),
                    state=_text(state.get("Name") if isinstance(state, Mapping) else state).casefold(),
                    created=to_date(item.get("LaunchTime")), state_changed=to_date(reason.group(1)) if reason else None,
                    instance_type=_text(item.get("InstanceType")).casefold(),
                    volume_ids=tuple(_text(e.get("VolumeId")) for e in mappings
                                     if isinstance(e, Mapping) and e.get("VolumeId")), tags=tags)


def _snapshot(item: Any) -> Resource | None:
    if not isinstance(item, Mapping) or not item.get("SnapshotId"):
        return None
    description = _text(item.get("Description"))
    tags = _aws_tags(item)
    return Resource(id=str(item["SnapshotId"]), kind="snapshot", name=_name_tag(tags) or description[:60],
                    state=_text(item.get("State")).casefold(), created=to_date(item.get("StartTime")),
                    size_gb=to_number(item.get("VolumeSize")), source_volume_id=_text(item.get("VolumeId")),
                    referenced_by_ami=True if _AMI.search(description) else None, tags=tags)


def _address(item: Any) -> Resource | None:
    if not isinstance(item, Mapping) or not (item.get("AllocationId") or item.get("PublicIp")):
        return None
    target = _text(item.get("AssociationId") or item.get("InstanceId") or item.get("NetworkInterfaceId"))
    tags = _aws_tags(item)
    return Resource(id=str(item.get("AllocationId") or item.get("PublicIp")), kind="address",
                    name=_name_tag(tags) or _text(item.get("PublicIp")), attached_to=target, attached=bool(target),
                    created=to_date(item.get("AllocationTime")), tags=tags)


def _load_balancer(item: Any) -> Resource | None:
    if not isinstance(item, Mapping) or not (item.get("LoadBalancerArn") or item.get("LoadBalancerName")):
        return None
    state = item.get("State")
    return Resource(id=str(item.get("LoadBalancerArn") or item["LoadBalancerName"]), kind="load_balancer",
                    name=_text(item.get("LoadBalancerName")), created=to_date(item.get("CreatedTime")),
                    state=_text(state.get("Code") if isinstance(state, Mapping) else state).casefold(),
                    tags=_aws_tags(item))


def _nat(item: Any) -> Resource | None:
    if not isinstance(item, Mapping) or not item.get("NatGatewayId"):
        return None
    tags = _aws_tags(item)
    return Resource(id=str(item["NatGatewayId"]), kind="nat_gateway", name=_name_tag(tags),
                    state=_text(item.get("State")).casefold(), created=to_date(item.get("CreateTime")), tags=tags)


# --- cross references and values -----------------------------------------------------------------

def _dedupe(resources: Sequence[Resource]) -> tuple[Resource, ...]:
    by_id: dict[str, Resource] = {}
    for r in resources:
        by_id.setdefault(r.id, r)
    return tuple(by_id.values())


def _with_cross_refs(resources: Sequence[Resource]) -> tuple[Resource, ...]:
    """What one pasted list says about another: a snapshot whose source volume is not among pasted volumes
    has no source (only when volumes were pasted at all); an instance's volumes are the pasted volumes
    attached to it."""
    volumes = {r.id: r for r in resources if r.kind == "volume"}
    attached: dict[str, tuple[str, ...]] = {}
    for v in volumes.values():
        for instance_id in (p.strip() for p in v.attached_to.split(",") if p.strip()):
            attached = {**attached, instance_id: (*attached.get(instance_id, ()), v.id)}
    out: tuple[Resource, ...] = ()
    for r in resources:
        if r.kind == "snapshot" and r.source_exists is None and volumes and r.source_volume_id:
            r = replace(r, source_exists=r.source_volume_id in volumes)
        if r.kind == "instance" and not r.volume_ids and r.id in attached:
            r = replace(r, volume_ids=attached[r.id])
        out = (*out, r)
    return out


def _text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _is_empty(value: Any) -> bool:
    if isinstance(value, (list, dict)):
        return not value
    return _text(value).casefold() in _EMPTY


def to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = _text(value).casefold()
    return True if text in _TRUE else False if text in _FALSE else None


def to_number(value: Any) -> float | None:
    """`500`, `"500 GB"`, `"$40.00"`, `"2.1%"`, `"5,000,000"` → the number; anything else → None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _text(value).replace(",", "")
    if text.casefold() in _EMPTY:
        return None
    cleaned = _UNIT.sub("", text.lstrip("$").strip())
    try:
        return float(cleaned)
    except ValueError:
        return None


def to_money(value: Any) -> Decimal | None:
    number = to_number(value)
    if number is None:
        return None
    try:
        return Decimal(str(number))
    except InvalidOperation:
        return None


def to_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 10**8:
        return datetime.fromtimestamp(float(value), UTC).date()
    text = _text(value)
    if not text or text.casefold() in _EMPTY:
        return None
    if text.isdigit() and len(text) == 10:
        return datetime.fromtimestamp(int(text), UTC).date()
    candidate = text.replace("/", "-").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        try:
            return date.fromisoformat(candidate[:10])
        except ValueError:
            return None
    return parsed.astimezone(UTC).date() if parsed.tzinfo else parsed.date()


def _tag_pairs(value: Any) -> tuple[tuple[str, str], ...]:
    """`[{"Key": k, "Value": v}]`, `{"k": "v"}` or `"k=v; k2=v2"` → ((k, v), ...)."""
    if isinstance(value, list):
        return tuple((_text(t.get("Key") or t.get("key")), _text(t.get("Value") or t.get("value")))
                     for t in value if isinstance(t, Mapping) and _text(t.get("Key") or t.get("key")))
    if isinstance(value, Mapping):
        return tuple((_text(k), _text(v)) for k, v in value.items() if _text(k))
    pairs: tuple[tuple[str, str], ...] = ()
    for part in re.split(r"[;|]", _text(value)):
        key, _, val = part.partition("=") if "=" in part else part.partition(":")
        if key.strip():
            pairs = (*pairs, (key.strip(), val.strip()))
    return pairs


# --- state round trip ----------------------------------------------------------------------------

def to_state(resource: Resource) -> dict:
    data = asdict(resource)
    for key in ("created", "last_activity", "state_changed"):
        data[key] = data[key].isoformat() if data[key] else None
    data["monthly_cost"] = str(resource.monthly_cost) if resource.monthly_cost is not None else None
    data["tags"] = [list(t) for t in resource.tags]
    data["volume_ids"] = list(resource.volume_ids)
    return data


def from_state(data: Mapping[str, Any]) -> Resource:
    known = {f.name for f in fields(Resource)}
    values = {k: v for k, v in data.items() if k in known}
    for key in ("created", "last_activity", "state_changed"):
        values[key] = date.fromisoformat(values[key]) if values.get(key) else None
    values["monthly_cost"] = Decimal(values["monthly_cost"]) if values.get("monthly_cost") else None
    values["tags"] = tuple(tuple(t) for t in values.get("tags") or ())
    values["volume_ids"] = tuple(values.get("volume_ids") or ())
    return Resource(**values)
