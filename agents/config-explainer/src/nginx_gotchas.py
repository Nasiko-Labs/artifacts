"""Pure: nginx gotchas N01–N14, each tied to a line. Upstream paths in N01 are computed by the same
resolver that answers "what handles this URL?"."""

from __future__ import annotations

import re

import nginx_match as m
from gotchas import Gotcha
from nginx_parse import Directive, all_locations, location_key, walk

SAMPLE_URI = "/api/x"
_WEAK_TLS = ("TLSv1", "TLSv1.1", "SSLv3", "SSLv2")
_WEAK_CIPHERS = re.compile(r"(?<![!\w])(RC4|DES|MD5|3DES)", re.IGNORECASE)
_INHERITED = ("add_header", "proxy_set_header")
_IF_OK = ("return", "rewrite")


def sample_uri(path: str) -> str:
    return path.rstrip("/") + "/x" if path not in ("", "/") else SAMPLE_URI


def config_gotchas(tree: tuple[Directive, ...], servers: list[Directive], url: str | None) -> list[Gotcha]:
    found: list[Gotcha] = []
    upstreams = {d.args[0] for d in walk(tree) if d.name == "upstream" and d.args}
    zones = _zones(tree)
    http = next((d for d in tree if d.name == "http"), None)
    for server in servers:
        chain_root = [b for b in (http, server) if b is not None]
        for location in all_locations(server):
            chain = [*chain_root, *m.parents(server, location)[1:]]
            found += _location_gotchas(location, chain, upstreams, url)
        found += _inheritance(server, chain_root)
        found += _server_gotchas(server)
    for directive in walk(tree):
        found += _directive_gotchas(directive, zones)
    if (http or servers) and not any(d.name == "server_tokens" and d.args[:1] == ("off",) for d in walk(tree)):
        found.append(Gotcha("N07", "info", "`server_tokens` is not `off`: error pages and the Server header show "
                            "the nginx version.", "server_tokens"))
    return found


def _zones(tree: tuple[Directive, ...]) -> set[str]:
    zones = set()
    for d in walk(tree):
        for arg in d.args:
            if arg.startswith(("zone=", "keys_zone=")):
                zones.add(arg.split("=", 1)[1].split(":")[0])
    return zones


def _location_gotchas(location: Directive, chain: list[Directive], upstreams: set[str], url: str | None) -> list:
    found: list[Gotcha] = []
    modifier, path = location_key(location)
    where = f"location `{path}` (line {location.line})"
    proxy = location.first("proxy_pass")
    if proxy is not None and proxy.args and modifier in ("", "^~") and path not in ("", "/"):
        uri = url if url and url.startswith(path) else sample_uri(path)
        target = proxy.args[0]
        if not m.VARIABLE.search(target):
            host = target.split("://", 1)[-1].split("/", 1)[0]
            with_slash = Directive("proxy_pass", (f"{target.split('://')[0]}://{host}/",), proxy.line)
            without = Directive("proxy_pass", (f"{target.split('://')[0]}://{host}",), proxy.line)
            found.append(Gotcha("N01", "medium", f"In {where}, `proxy_pass {target}` sends `{uri}` upstream as "
                                f"`{m.proxy_target(location, uri, proxy)}`. A trailing slash matters: "
                                f"`{without.args[0]}` would send `{m.proxy_target(location, uri, without)}`, "
                                f"`{with_slash.args[0]}` would send `{m.proxy_target(location, uri, with_slash)}`.",
                                f"line {proxy.line}"))
    if proxy is not None and proxy.args:
        host = proxy.args[0].split("://", 1)[-1].split("/", 1)[0].split(":")[0]
        looks_named = "." not in host and host != "localhost" and not m.VARIABLE.search(host)
        if looks_named and host not in upstreams:
            found.append(Gotcha("N05", "medium", f"`proxy_pass` on line {proxy.line} names `{host}`, but no "
                                "`upstream` block with that name is in this file (it may be in an include).",
                                f"line {proxy.line}"))
        if m.setting(chain, "proxy_set_header") is None or not any(
                d.args[:1] == ("Host",) for block in chain for d in block.children("proxy_set_header")):
            found.append(Gotcha("N10", "medium", f"{where} proxies without `proxy_set_header Host`: the upstream "
                                "sees the upstream's own name as Host, not the client's.", f"line {proxy.line}"))
    alias = location.first("alias")
    if alias is not None and alias.args and modifier in ("", "^~") and path.endswith("/") != alias.args[0].endswith("/"):
        found.append(Gotcha("N02", "low", f"In {where} the location and `alias {alias.args[0]}` disagree on the "
                            "trailing slash: requests map to odd paths (`//` or a path traversal like `/static../`).",
                            f"line {alias.line}"))
    for block in location.children("if"):
        bad = [d.name for d in block.block or () if d.name not in _IF_OK]
        if bad:
            found.append(Gotcha("N03", "medium", f"`if` inside {where} contains `{', '.join(bad)}`; only `return` and "
                                "`rewrite` are safe inside `if` in a location (\"if is evil\").", f"line {block.line}"))
    if modifier in ("~", "~*") and r"\.php" in path and location.first("try_files") is None:
        found.append(Gotcha("N06", "low", f"{where} passes `.php` requests on without `try_files $uri =404`, so "
                            "non-existent scripts reach the PHP backend.", f"line {location.line}"))
    if "upload" in path and m.setting(chain, "client_max_body_size") is None:
        found.append(Gotcha("N11", "info", f"{where} looks like an upload endpoint but `client_max_body_size` is "
                            "not set: the default is 1m and bigger uploads get 413.", f"line {location.line}"))
    if location.first("root") is not None and chain and chain[0].first("root") is None:
        found.append(Gotcha("N14", "low", f"`root` is set inside {where}; setting it once at server level gives "
                            "other locations a sane default.", f"line {location.first('root').line}"))
    return found


def _inheritance(server: Directive, chain_root: list[Directive]) -> list[Gotcha]:
    found = []
    for name in _INHERITED:
        parent = [d.args[0] for block in chain_root for d in block.children(name) if d.args]
        if not parent:
            continue
        for location in all_locations(server):
            own = [d.args[0] for d in location.children(name) if d.args]
            lost = [h for h in parent if h not in own]
            if own and lost:
                found.append(Gotcha("N04", "medium", f"location `{location_key(location)[1]}` (line {location.line}) "
                                    f"sets its own `{name}`, so it loses the inherited ones: {', '.join(lost)}.",
                                    f"line {location.line}"))
    return found


def _server_gotchas(server: Directive) -> list[Gotcha]:
    found = []
    if server.children("listen") and not server.children("server_name"):
        found.append(Gotcha("N14", "low", f"The server on line {server.line} has `listen` but no `server_name`: it "
                            "catches requests for any host on that port if it is the default.", f"line {server.line}"))
    locs = all_locations(server)
    for regex in (loc for loc in locs if location_key(loc)[0] in ("~", "~*")):
        compiled = m.nginx_regex(location_key(regex)[1], location_key(regex)[0] == "~*")
        for prefix in (loc for loc in locs if location_key(loc)[0] == ""):
            path = location_key(prefix)[1]
            if compiled is not None and len(path) > 1 and compiled.search(sample_uri(path)):
                found.append(Gotcha("N13", "low", f"The regex location `{location_key(regex)[1]}` (line {regex.line}) "
                                    f"wins over the prefix `{path}` (line {prefix.line}) for `{sample_uri(path)}`; "
                                    "use `^~` on the prefix if it should win.", f"line {regex.line}"))
    return found


def _directive_gotchas(d: Directive, zones: set[str]) -> list[Gotcha]:
    found = []
    if d.name == "autoindex" and d.args[:1] == ("on",):
        found.append(Gotcha("N08", "low", f"`autoindex on` (line {d.line}) lists directory contents to anyone.",
                            f"line {d.line}"))
    if d.name == "ssl_protocols" and any(a in _WEAK_TLS for a in d.args):
        weak = [a for a in d.args if a in _WEAK_TLS]
        found.append(Gotcha("N09", "medium", f"`ssl_protocols` (line {d.line}) still allows {', '.join(weak)}.",
                            f"line {d.line}"))
    if d.name == "ssl_ciphers" and d.args and _WEAK_CIPHERS.search(d.args[0]):
        found.append(Gotcha("N09", "medium", f"`ssl_ciphers` (line {d.line}) includes a weak cipher family.",
                            f"line {d.line}"))
    if d.name in ("limit_req", "limit_conn", "proxy_cache"):
        zone = next((a.split("=", 1)[1] for a in d.args if a.startswith("zone=")), d.args[0] if d.args else "")
        used = d.name != "proxy_cache" or zone != "off"
        if used and zone and zone not in zones and not zone.startswith("$"):
            found.append(Gotcha("N05", "medium", f"`{d.name}` on line {d.line} uses zone `{zone}`, which isn't "
                                "defined in this file.", f"line {d.line}"))
    if d.block is not None:
        found += _access_order(d)
    return found


def _access_order(block: Directive) -> list[Gotcha]:
    rules = [d for d in block.block or () if d.name in ("allow", "deny")]
    for index, rule in enumerate(rules):
        if rule.args[:1] == ("all",) and index < len(rules) - 1:
            return [Gotcha("N12", "low", f"`{rule.name} all` on line {rule.line} comes before other allow/deny "
                           "rules; the first match wins, so the later ones never apply.", f"line {rule.line}")]
    return []
