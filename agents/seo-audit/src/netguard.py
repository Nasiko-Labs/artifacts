"""SSRF guard for the one page a user asks to audit (logic modelled on the guard other agents carry; this is
this agent's own copy, not a shared module).

Deny by default for everything non-public:
1. http/https only; ports 80/443 only; no userinfo; host <= 253 chars, IDNA-encoded; single-label and
   local-only names refused; IP literals in any encoding (dotted, decimal, hex, octal, IPv6) are decoded and
   must be public.
2. ALL resolved A/AAAA records must be globally routable (IPv4-mapped / 6to4 / Teredo IPv6 are unwrapped).
3. The caller connects to the validated IP (`Target.pinned_url`), never re-resolving the name; the Host
   header and TLS SNI keep the original name, so the certificate is still checked against it.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

ALLOWED_SCHEMES = ("http", "https")
ALLOWED_PORTS = (80, 443)
MAX_HOST_LEN = 253
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa", ".lan", ".intranet")
_LEGACY_IPV4 = re.compile(r"^(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]*|[0-9]+)){0,3}$", re.IGNORECASE)

Resolver = Callable[[str, int], Awaitable[list[str]]]


class GuardError(Exception):
    """The URL was refused (the message is shown to the user)."""


@dataclass(frozen=True)
class Target:
    url: str            # the normalised URL (IDNA host)
    scheme: str
    host: str           # IDNA host, for SNI and the Host header
    port: int
    ip: str             # the validated address the connection goes to
    path: str           # path + query, for robots matching

    @property
    def host_header(self) -> str:
        default = 443 if self.scheme == "https" else 80
        return self.host if self.port == default else f"{self.host}:{self.port}"

    @property
    def pinned_url(self) -> str:
        ip = ipaddress.ip_address(self.ip)
        literal = f"[{ip}]" if ip.version == 6 else str(ip)
        default = 443 if self.scheme == "https" else 80
        netloc = literal if self.port == default else f"{literal}:{self.port}"
        path, _, query = self.path.partition("?")
        return urlunsplit((self.scheme, netloc, path or "/", query, ""))


async def system_resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


def parse_url(url: str) -> tuple[str, str, int, str, str | None]:
    """(scheme, idna host, port, path, ip literal or None); raises GuardError. No network."""
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError as e:
        raise GuardError("that URL is malformed") from e
    scheme = parts.scheme.casefold()
    if scheme not in ALLOWED_SCHEMES:
        raise GuardError("only http and https pages can be fetched")
    if "@" in parts.netloc:
        raise GuardError("URLs with a user name or password are refused")
    port = port or (443 if scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise GuardError(f"port {port} is refused (only 80 and 443)")
    host = (parts.hostname or "").rstrip(".")
    if not host or len(host) > MAX_HOST_LEN:
        raise GuardError("that URL has no valid host name")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    literal = ip_literal(host)
    if literal is not None:
        return scheme, literal, port, path, literal
    try:
        idna = host.encode("idna").decode("ascii").casefold()
    except UnicodeError as e:
        raise GuardError("that host name is not valid") from e
    if "." not in idna or idna == "localhost" or idna.endswith(_LOCAL_SUFFIXES):
        raise GuardError(f"{idna} is a local name, not a public site")
    return scheme, idna, port, path, None


def ip_literal(host: str) -> str | None:
    """The address a host written as an IP literal (any encoding) stands for, else None."""
    try:
        return str(ipaddress.ip_address(host.strip("[]")))
    except ValueError:
        pass
    if _LEGACY_IPV4.match(host):
        try:
            return socket.inet_ntoa(socket.inet_aton(host))
        except OSError as e:
            raise GuardError("that host looks like an IP address but is not valid") from e
    return None


def is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6:
        for embedded in (ip.ipv4_mapped, ip.sixtofour, (ip.teredo or (None, None))[1]):
            if embedded is not None and not embedded.is_global:
                return False
    return ip.is_global and not ip.is_multicast


async def validate(url: str, resolve: Resolver) -> Target:
    """One validation hop: parse, resolve once, require every address public, pin the first."""
    scheme, host, port, path, literal = parse_url(url)
    if literal is not None:
        addresses = [literal]
    else:
        try:
            addresses = await resolve(host, port)
        except OSError as e:
            raise GuardError(f"{host} could not be resolved") from e
    if not addresses:
        raise GuardError(f"{host} could not be resolved")
    if not all(is_public(a) for a in addresses):
        raise GuardError(f"{host} points to a private or reserved address, which is never fetched")
    netloc = host if port == (443 if scheme == "https" else 80) else f"{host}:{port}"
    normal = urlunsplit((scheme, netloc if literal is None else _bracket(host, port, scheme), *_split_path(path), ""))
    return Target(url=normal, scheme=scheme, host=host, port=port, ip=addresses[0], path=path)


def _bracket(ip: str, port: int, scheme: str) -> str:
    literal = f"[{ip}]" if ":" in ip else ip
    return literal if port == (443 if scheme == "https" else 80) else f"{literal}:{port}"


def _split_path(path: str) -> tuple[str, str]:
    p, _, q = path.partition("?")
    return p, q
