"""The guarded fetch of ONE page plus robots.txt and llms.txt on the same host (3 documents per turn).

Every hop goes through `netguard.validate` and connects to the validated IP. Redirects are followed by hand
(page only, <= 3, each re-validated, https -> http refused). Bodies are streamed raw and decompressed here
with a hard cap, so a gzip bomb never expands past it. No cookies, no auth, a fixed User-Agent. robots.txt
is read first and honoured for our agent; a Disallow refuses the page fetch."""

from __future__ import annotations

import asyncio
import time
import zlib
from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx

import netguard
import robots
from nasiko_trace import suppressed_http_instrumentation

USER_AGENT = "NasikoSEOAudit/1.0 (+one-page audit; no crawling)"
PAGE_MAX_BYTES = 2 * 1024 * 1024
TEXT_MAX_BYTES = 200 * 1024
REQUEST_TIMEOUT_SECS = 15.0
TURN_FETCH_SECS = 30.0
MAX_REDIRECTS = 3
MAX_DOCUMENTS = 3
PAGE_TYPES = ("text/html", "application/xhtml+xml")
TEXT_TYPES = ("text/plain", "text/markdown")


class FetchError(Exception):
    """A document could not be fetched (message shown to the user)."""


@dataclass
class FetchResult:
    html: str = ""
    final_url: str = ""
    redirects: list[str] = field(default_factory=list)
    robots_txt: str | None = None     # None: not found / not read
    llms_found: bool | None = None    # None: not read
    error: str = ""
    documents: int = 0


class Fetcher:
    def __init__(self, resolve: netguard.Resolver = netguard.system_resolve,
                 transport: httpx.AsyncBaseTransport | None = None, request_timeout: float = REQUEST_TIMEOUT_SECS,
                 turn_budget: float = TURN_FETCH_SECS, clock=time.monotonic):
        self._resolve, self._transport = resolve, transport
        self._request_timeout, self._turn_budget, self._clock = request_timeout, turn_budget, clock

    async def fetch(self, url: str) -> FetchResult:
        result = FetchResult()
        deadline = self._clock() + self._turn_budget
        try:
            scheme, host, port, path, literal = netguard.parse_url(url)  # no network yet
            netloc = f"[{host}]" if literal and ":" in host else host
            base = f"{scheme}://{netloc}" + ("" if port == (443 if scheme == "https" else 80) else f":{port}")
            result.robots_txt = await self._optional(f"{base}/robots.txt", result, deadline)
            rule = robots.verdict(robots.parse(result.robots_txt or ""), robots.OUR_AGENT, path)
            if not rule.allowed:
                raise FetchError(f"the site's robots.txt disallows this path for crawlers ({rule.rule.line})")
            page, llms = await asyncio.gather(self._page(base + path, result, deadline),
                                              self._optional(f"{base}/llms.txt", result, deadline))
            result.html, result.llms_found = page, llms is not None
        except (netguard.GuardError, FetchError) as e:
            result.error = str(e)
        return result

    async def _optional(self, url: str, result: FetchResult, deadline: float) -> str | None:
        """robots.txt / llms.txt: no redirects, text only, small cap; any failure is "not found"."""
        try:
            status, ctype, body, _ = await self._get(url, result, deadline, TEXT_MAX_BYTES)
        except (netguard.GuardError, FetchError):
            return None
        if status != 200 or not ctype.startswith(TEXT_TYPES):
            return None
        return body.decode("utf-8", errors="replace")

    async def _page(self, url: str, result: FetchResult, deadline: float) -> str:
        for _ in range(MAX_REDIRECTS + 1):
            status, ctype, body, location = await self._get(url, result, deadline, PAGE_MAX_BYTES)
            if status in (301, 302, 303, 307, 308) and location:
                nxt = urljoin(url, location)
                if url.startswith("https:") and nxt.startswith("http:"):
                    raise FetchError("the page redirects from https to http, which is refused")
                result.redirects.append(nxt)
                url = nxt
                continue
            if status != 200:
                raise FetchError(f"the page answered HTTP {status}")
            if not ctype.startswith(PAGE_TYPES):
                raise FetchError(f"the URL is not an HTML page ({ctype or 'no content type'})")
            result.final_url = url
            return body.decode("utf-8", errors="replace")
        raise FetchError(f"the page redirects more than {MAX_REDIRECTS} times")

    async def _get(self, url: str, result: FetchResult, deadline: float, cap: int) -> tuple[int, str, bytes, str]:
        target = await netguard.validate(url, self._resolve)
        if result.documents >= MAX_DOCUMENTS + MAX_REDIRECTS:
            raise FetchError("the request budget for this turn is used up")
        result.documents += 1
        timeout = min(self._request_timeout, deadline - self._clock())
        if timeout <= 0:
            raise FetchError("the fetch time budget for this turn ran out")
        try:
            return await asyncio.wait_for(self._send(target, cap), timeout=timeout)
        except TimeoutError as e:
            raise FetchError(f"the site did not answer within {timeout:.0f} s") from e
        except httpx.HTTPError as e:
            raise FetchError(f"the site could not be reached ({type(e).__name__})") from e

    async def _send(self, target: netguard.Target, cap: int) -> tuple[int, str, bytes, str]:
        headers = {"Host": target.host_header, "User-Agent": USER_AGENT, "Accept-Encoding": "gzip",
                   "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8"}
        with suppressed_http_instrumentation():  # never leak the platform's trace id to a third-party site
            async with httpx.AsyncClient(transport=self._transport, follow_redirects=False, trust_env=False) as http:
                request = http.build_request("GET", target.pinned_url, headers=headers,
                                             extensions={"sni_hostname": target.host})
                response = await http.send(request, stream=True)
                try:
                    ctype = response.headers.get("content-type", "").split(";")[0].strip().casefold()
                    body = b"" if response.is_redirect else await _read_capped(response, cap)
                    return response.status_code, ctype, body, response.headers.get("location", "")
                finally:
                    await response.aclose()


async def _read_capped(response: httpx.Response, cap: int) -> bytes:
    encoding = response.headers.get("content-encoding", "identity").strip().casefold()
    if encoding not in ("identity", "gzip", ""):
        raise FetchError(f"unsupported content encoding {encoding}")
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
    chunks: list[bytes] = []
    size = 0
    async for raw in response.aiter_raw():
        piece = raw
        if inflater is not None:
            piece = inflater.decompress(raw, cap + 1 - size)
            if inflater.unconsumed_tail:
                raise FetchError(f"the response expands past the {cap // 1024} KB cap and was refused")
        size += len(piece)
        if size > cap:
            raise FetchError(f"the response is larger than the {cap // 1024} KB cap and was refused")
        chunks.append(piece)
    return b"".join(chunks)
