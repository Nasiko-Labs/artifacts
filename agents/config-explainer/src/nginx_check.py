"""Pure: the nginx analysis. Parse tree facts, what handles each asked URL (server, location, rewrites,
return, file path, upstream URL), and the gotchas (nginx_gotchas.py). Includes are listed, never resolved."""

from __future__ import annotations

from urllib.parse import urlsplit

import nginx_match as m
from analysis import Analysis
from gotchas import ordered
from nginx_gotchas import config_gotchas
from nginx_parse import Directive, all_locations, location_key, parse, servers, walk
from probes import Probes

HONESTY = "I don't resolve `include` files or check third-party module directives."


def analyze(config: str, probes: Probes) -> Analysis:
    parsed = parse(config)
    tree = parsed.tree
    found_servers = servers(tree)
    if not found_servers and tree:
        found_servers = [Directive("server", (), 1, tree)]  # a pasted location-only snippet
    facts = {"servers": [_server_facts(s) for s in found_servers], "upstreams": _upstreams(tree),
             "unresolved_includes": list(parsed.includes)}
    if parsed.error:
        facts["syntax_error"] = parsed.error
        return Analysis("nginx", config, facts, (), parse_error=f"Syntax error, {parsed.error}",
                        summary=f"nginx config: syntax error ({parsed.error}); explained up to there.")
    checked: list[str] = []
    resolved = []
    for url in probes.urls:
        result = resolve(found_servers, url, probes.host)
        resolved.append(result)
        checked.append(result["line"])
    facts["requests"] = resolved
    gotchas = ordered(config_gotchas(tree, found_servers, probes.urls[0] if probes.urls else None))
    names = [loc for s in found_servers for loc in all_locations(s)]
    summary = (f"nginx config: {len(found_servers)} server block(s), {len(names)} location(s); "
               f"{len(gotchas)} gotchas.")
    mentions = tuple(dict.fromkeys(location_key(loc)[1] for loc in names))
    return Analysis("nginx", config, facts, tuple(gotchas), checked=tuple(checked), summary=summary,
                    partial=bool(parsed.includes), must_mention=mentions, notes=(HONESTY,))


def _server_facts(server: Directive) -> dict:
    return {"line": server.line, "listen": [" ".join(d.args) for d in server.children("listen")],
            "server_name": [n for d in server.children("server_name") for n in d.args],
            "locations": [_location_facts(loc) for loc in all_locations(server)],
            "directives": [f"{d.name} {' '.join(d.args)}".strip() for d in server.block or () if d.block is None]}


def _location_facts(location: Directive) -> dict:
    modifier, path = location_key(location)
    return {"line": location.line, "modifier": modifier or "prefix", "path": path,
            "directives": [f"{d.name} {' '.join(d.args)}".strip() for d in location.block or () if d.block is None]}


def _upstreams(tree: tuple[Directive, ...]) -> dict:
    return {d.args[0]: [" ".join(s.args) for s in d.children("server")]
            for d in walk(tree) if d.name == "upstream" and d.args}


def _split(url: str, host: str | None) -> tuple[str, str | None, int]:
    if url.startswith(("http://", "https://")):
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        return (parts.path or "/") + (f"?{parts.query}" if parts.query else ""), host or parts.hostname, port
    return url, host, 80


def resolve(found_servers: list[Directive], url: str, host: str | None) -> dict:
    uri, host_name, port = _split(url, host)
    path = uri.split("?")[0]
    server = m.select_server(found_servers, host_name, port)
    if server is None:
        return {"request": url, "line": f"`{url}` → no server block found."}
    path, flag, steps = m.apply_rewrites(server, path)  # server-level rewrites run once
    if flag and flag != "last":
        return _done(url, server, None, steps, f"ends with {flag}")
    location = None
    for _ in range(m.MAX_REWRITE_CYCLES):
        location = m.select_location(server, path)
        if location is None:
            break
        new_path, flag, loc_steps = m.apply_rewrites(location, path)
        steps += loc_steps
        if flag == "last" and new_path != path:
            path = new_path
            continue
        path = new_path
        if flag and flag != "break":
            return _done(url, server, location, steps, f"ends with {flag}")
        break
    return _done(url, server, location, steps, _outcome(server, location, path))


def _outcome(server: Directive, location: Directive | None, path: str) -> str:
    if location is None:
        return "no location matches, so nginx serves it from the server's root (or 404)"
    chain = m.parents(server, location)
    proxy = location.first("proxy_pass")
    if proxy is not None:
        return f"proxied to `{m.proxy_target(location, path, proxy)}`"
    tries = location.first("try_files")
    disk = m.file_path(chain, location, path)
    if tries is not None:
        return f"tries {' then '.join(f'`{a}`' for a in tries.args)}" + (f" (file `{disk}`)" if disk else "")
    return f"served from file `{disk}`" if disk else "handled by the location's own directives"


def _done(url: str, server: Directive, location: Directive | None, steps: list[str], outcome: str) -> dict:
    names = " ".join(n for d in server.children("server_name") for n in d.args) or "(no server_name)"
    where = "no location"
    if location is not None:
        modifier, path = location_key(location)
        where = f"location `{(modifier + ' ' if modifier else '') + path}` (line {location.line})"
    line = f"`{url}` → server `{names}` (line {server.line}) → {where}"
    if steps:
        line += " → " + " → ".join(steps)
    return {"request": url, "server_line": server.line, "location_line": location.line if location else None,
            "steps": steps, "outcome": outcome, "line": f"{line} → {outcome}."}

