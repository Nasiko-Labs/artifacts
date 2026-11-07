"""Pure: which server and location handle a request, and what happens to it, following nginx's own rules.

Location: exact `=` wins; else the longest prefix is remembered; `^~` on it stops the search; else the
first matching regex (`~`, `~*`) in file order; else the remembered prefix. Nested locations are searched
inside the chosen one. Then `rewrite`/`return`, `root`/`alias`, `try_files` and `proxy_pass` are resolved."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from nginx_parse import Directive, location_key, locations

MAX_REWRITE_CYCLES = 10
VARIABLE = re.compile(r"\$\w|\$\{")


def nginx_regex(pattern: str, insensitive: bool) -> re.Pattern | None:
    try:
        return re.compile(re.sub(r"\(\?<(?![=!])", "(?P<", pattern), re.IGNORECASE if insensitive else 0)
    except re.error:
        return None


def select_server(servers: list[Directive], host: str | None, port: int = 80) -> Directive | None:
    on_port = [s for s in servers if port in _ports(s)] or servers
    if not on_port:
        return None
    name = (host or "").casefold()
    if name:
        for test in (_exact, _leading, _trailing, _regex):
            for server in on_port:
                if any(test(n.casefold() if not n.startswith("~") else n, name) for n in _names(server)):
                    return server
    defaults = [s for s in on_port if any("default_server" in d.args for d in s.children("listen"))]
    return (defaults or on_port)[0]


def _ports(server: Directive) -> set[int]:
    ports = set()
    for listen in server.children("listen"):
        match = re.search(r"(\d+)$", listen.args[0].split("]")[-1]) if listen.args else None
        ports.add(int(match.group(1)) if match else 80)
    return ports or {80}


def _names(server: Directive) -> list[str]:
    return [n for d in server.children("server_name") for n in d.args]


def _exact(pattern: str, host: str) -> bool:
    return "*" not in pattern and not pattern.startswith("~") and pattern.lstrip(".") == host


def _leading(pattern: str, host: str) -> bool:
    return pattern.startswith("*.") and host.endswith(pattern[1:]) or (
        pattern.startswith(".") and (host == pattern[1:] or host.endswith(pattern)))


def _trailing(pattern: str, host: str) -> bool:
    return pattern.endswith(".*") and host.startswith(pattern[:-1])


def _regex(pattern: str, host: str) -> bool:
    compiled = nginx_regex(pattern[1:], True) if pattern.startswith("~") else None
    return bool(compiled and compiled.search(host))


def select_location(block: Directive, uri: str) -> Directive | None:
    found = locations(block)
    for location in found:
        modifier, path = location_key(location)
        if modifier == "=" and path == uri:
            return location
    prefixes = [loc for loc in found if location_key(loc)[0] in ("", "^~") and uri.startswith(location_key(loc)[1])]
    best = max(prefixes, key=lambda loc: len(location_key(loc)[1]), default=None)
    if best is not None:
        nested = select_location(best, uri) if locations(best) else None
        if nested is not None:
            return nested
        if location_key(best)[0] == "^~":
            return best
    for location in found:
        modifier, path = location_key(location)
        compiled = nginx_regex(path, modifier == "~*") if modifier in ("~", "~*") else None
        if compiled is not None and compiled.search(uri):
            return location
    return best


def setting(chain: list[Directive], name: str) -> Directive | None:
    """The innermost directive `name` along server → location chain (nginx inheritance)."""
    for block in reversed(chain):
        found = block.first(name)
        if found is not None:
            return found
    return None


def parents(server: Directive, target: Directive) -> list[Directive]:
    """server → … → target."""
    def search(block: Directive, path: list[Directive]) -> list[Directive] | None:
        if block is target:
            return path
        for child in locations(block):
            hit = search(child, [*path, child])
            if hit:
                return hit
        return None
    return search(server, [server]) or [server]


def apply_rewrites(block: Directive, uri: str) -> tuple[str, str | None, list[str]]:
    """(new uri, flag or return, steps). Flags: last, break, redirect, permanent; None when no rewrite hit."""
    steps = []
    for directive in block.block or ():
        if directive.name == "return" and directive.args:
            steps.append(f"`return {' '.join(directive.args)}` (line {directive.line})")
            return uri, "return " + " ".join(directive.args), steps
        if directive.name != "rewrite" or len(directive.args) < 2:
            continue
        compiled = nginx_regex(directive.args[0], False)
        match = compiled.search(uri) if compiled else None
        if not match:
            continue
        replacement = re.sub(r"\$(\d)", lambda ref, hit=match: hit.group(int(ref.group(1))) or "", directive.args[1])
        flag = directive.args[2] if len(directive.args) > 2 else None
        steps.append(f"`rewrite` on line {directive.line}: `{uri}` → `{replacement}`" + (f" ({flag})" if flag else ""))
        uri = replacement
        if flag in ("last", "break", "redirect", "permanent") or replacement.startswith(("http://", "https://")):
            return uri, flag or "redirect", steps
    return uri, None, steps


def proxy_target(location: Directive, uri: str, proxy: Directive) -> str:
    """The upstream URL nginx requests for `uri` under `location`."""
    target = proxy.args[0] if proxy.args else ""
    if VARIABLE.search(target):
        return f"{target} (has variables: passed as written, no URI replacement)"
    parts = urlsplit(target)
    base = f"{parts.scheme}://{parts.netloc}"
    modifier, path = location_key(location)
    if not parts.path:
        return base + uri
    if modifier in ("~", "~*", "@"):
        return f"{target} (a URI part is not allowed in a regex or named location: nginx refuses this config)"
    return base + parts.path + uri[len(path):] if uri.startswith(path) else base + parts.path


def file_path(chain: list[Directive], location: Directive, uri: str) -> str | None:
    alias = location.first("alias")
    if alias is not None and alias.args:
        prefix = location_key(location)[1]
        return alias.args[0] + uri[len(prefix):]
    root = setting(chain, "root")
    return (root.args[0].rstrip("/") + uri) if root is not None and root.args else None
