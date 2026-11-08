"""SSRF guard for fetching a download link a storage tool returned.

https only; the host is resolved and EVERY address must be globally routable; the connection is pinned to
the validated address (SNI and the Host header keep the real name, so TLS is still checked against it);
at most 3 redirects, each re-validated; the body is streamed with a 20 MB cap; 30 s overall timeout."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

MAX_BYTES = 20 * 1024 * 1024
TIMEOUT_SECS = 30.0
MAX_REDIRECTS = 3

Resolver = Callable[[str, int], Awaitable[list[str]]]


class GuardError(Exception):
    """The link was refused or could not be fetched (the message is written for logs and notes)."""


async def system_resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


class SafeFetcher:
    def __init__(self, resolve: Resolver = system_resolve, transport: httpx.AsyncBaseTransport | None = None,
                 max_bytes: int = MAX_BYTES, timeout: float = TIMEOUT_SECS):
        self._resolve = resolve
        self._transport = transport
        self._max_bytes = max_bytes
        self._timeout = timeout

    async def fetch(self, url: str) -> bytes:
        try:
            return await asyncio.wait_for(self._follow(url), timeout=self._timeout)
        except TimeoutError as e:
            raise GuardError(f"timed out after {self._timeout:.0f}s") from e

    async def _follow(self, url: str) -> bytes:
        async with httpx.AsyncClient(transport=self._transport, follow_redirects=False, timeout=self._timeout) as http:
            for _ in range(MAX_REDIRECTS + 1):
                pinned, host = await self._pin(url)
                request = http.build_request("GET", pinned, headers={"Host": host},
                                             extensions={"sni_hostname": urlsplit(url).hostname})
                response = await http.send(request, stream=True)
                try:
                    if response.is_redirect and response.headers.get("location"):
                        url = urljoin(url, response.headers["location"])
                        continue
                    if response.status_code != 200:
                        raise GuardError(f"HTTP {response.status_code}")
                    return await self._body(response)
                finally:
                    await response.aclose()
        raise GuardError(f"more than {MAX_REDIRECTS} redirects")

    async def _pin(self, url: str) -> tuple[str, str]:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise GuardError("only https links are fetched")
        port = parts.port or 443
        try:
            addresses = await self._resolve(parts.hostname, port)
        except OSError as e:
            raise GuardError(f"cannot resolve {parts.hostname}") from e
        if not addresses or not all(_is_public(a) for a in addresses):
            raise GuardError(f"{parts.hostname} resolves to a non-public address")
        ip = ipaddress.ip_address(addresses[0])
        literal = f"[{ip}]" if ip.version == 6 else str(ip)
        netloc = literal if parts.port is None else f"{literal}:{parts.port}"
        host = parts.hostname if parts.port is None else f"{parts.hostname}:{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, "")), host

    async def _body(self, response: httpx.Response) -> bytes:
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self._max_bytes:
            raise GuardError("the file is larger than 20 MB")
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self._max_bytes:
                raise GuardError("the file is larger than 20 MB")
            chunks.append(chunk)
        return b"".join(chunks)


def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global
