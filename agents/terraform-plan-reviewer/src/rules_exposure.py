"""Pure: EX01, exposure. Ingress opened to the whole internet on a non-web port, public access flags,
public ACLs and IAM members, and encryption or TLS turned down."""

from __future__ import annotations

import re
from collections.abc import Iterator

from changes import Change, Finding, changed, make, number, turned_false, turned_true
from rules_data import ENCRYPTED_STORES

OPEN_CIDR = re.compile(r"0\.0\.0\.0/0|::/0")
PUBLIC_MEMBERS = re.compile(r"allUsers|allAuthenticatedUsers")
WEB_PORTS = frozenset({80, 443})
CIDR_KEYS = ("cidr_blocks", "ipv6_cidr_blocks", "cidr_ipv4", "cidr_ipv6", "source_ranges", "source_address_prefix",
             "source_address_prefixes", "cidr_block")
TLS_ATTRS = ("min_tls_version", "minimum_tls_version", "ssl_minimal_tls_version_enforced")
MAX_PORT_SPAN = 1024


def ex01_exposure(c: Change) -> Iterator[Finding]:
    if c.action not in ("create", "update", "replace"):
        return
    yield from _open_ingress(c)
    flags = (("publicly_accessible", turned_true, "the resource becomes publicly accessible."),
             ("allow_blob_public_access", turned_true, "public blob access is allowed."),
             ("allow_nested_items_to_be_public", turned_true, "public blob access is allowed."))
    for name, test, reason in flags:
        if test(c.attr(name)):
            yield make("EX01", "high", c, reason, f"Should {c.address} be reachable from the internet?", c.attr(name))
    for a in c.attrs_named("block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets"):
        if c.action == "update" and turned_false(a):
            yield make("EX01", "high", c, f"{a.name} is turned off: the bucket can be made public.",
                       "Why relax the public access block?", a)
    acl = c.attr("acl")
    if acl is not None and (acl.after or "").startswith("public-read") and acl.before != acl.after:
        yield make("EX01", "high", c, f"the ACL becomes {acl.after}.", "Should these objects be public?", acl)
    if "iam" in c.resource_type and c.resource_type.startswith("google_") and \
            len(PUBLIC_MEMBERS.findall(c.after_text)) > len(PUBLIC_MEMBERS.findall(c.before_text)):
        yield make("EX01", "high", c, "access is granted to allUsers or allAuthenticatedUsers.",
                   "Should this be public?")
    yield from _encryption_off(c)


def _encryption_off(c: Change) -> Iterator[Finding]:
    if c.action == "create":
        return
    if c.resource_type not in ENCRYPTED_STORES:
        for a in c.attrs_named("storage_encrypted", "encrypted"):
            if turned_false(a):
                yield make("EX01", "high", c, "encryption at rest is turned off.", "Why turn encryption off?", a)
    for a in c.attrs_named(*TLS_ATTRS):
        if changed(a) and (a.after or "") < (a.before or ""):
            yield make("EX01", "high", c, f"the minimum TLS version is lowered to {a.after}.",
                       "Which client needs the older TLS version?", a)


def _open_ingress(c: Change) -> Iterator[Finding]:
    rtype = c.resource_type
    if "egress" in rtype or (c.attr("type") and (c.attr("type").after or "") == "egress"):
        return
    if (c.attr("direction") and (c.attr("direction").after or "").casefold() in ("egress", "outbound")):
        return
    opened = [a for a in c.attrs_named(*CIDR_KEYS) if not a.name.startswith("egress")
              and OPEN_CIDR.search(a.after or "") and not OPEN_CIDR.search(a.before or "")]
    if not opened:
        return
    ports = _ports(c)
    if ports and ports <= WEB_PORTS:
        return
    shown = ", ".join(str(p) for p in sorted(ports)) if ports else "all ports"
    yield make("EX01", "high", c, f"ingress from the whole internet ({opened[0].after}) on {shown}.",
               f"Why must {shown} on {c.address} be open to the internet?", opened[0])


def _first_after(c: Change, name: str) -> float | None:
    found = c.attrs_named(name)
    return number(found[0].after) if found else None


def _ports(c: Change) -> set[int]:
    """The ports opened; empty means all ports (a range of -1, 0-65535 or none given)."""
    low, high = _first_after(c, "from_port"), _first_after(c, "to_port")
    if low is not None and high is not None:
        if low < 0 or high - low > MAX_PORT_SPAN:
            return set()
        return set(range(int(low), int(high) + 1))
    ports: set[int] = set()
    for a in c.attrs_named("port", "ports", "destination_port_range", "destination_port_ranges"):
        ports |= {int(p) for p in re.findall(r"\d+", a.after or "")}
    return ports
