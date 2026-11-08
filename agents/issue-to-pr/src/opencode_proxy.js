// Loopback-only LLM proxy for OpenCode (copied approach from the opencode-agent's nasiko-shim.js /llm leg).
//
// Nasiko's LLM router refuses every call without the flow's W3C traceparent. OpenCode makes its provider
// calls from its own uninstrumented process, so its provider baseURL points here and this proxy adds the
// header. Improvement over "last request wins": the Python agent POSTs /_traceparent before every engine
// run (one run at a time per container), so each run is attributed to its own flow.
const http = require('http');
const { Readable } = require('stream');

const PORT = Number(process.env.PROXY_PORT || 8001);
const ROUTER_BASE = (process.env.LLM_ROUTER_BASE_URL || '').replace(/\/+$/, '');
const LLM_PREFIX = '/llm';
const DROP_RESPONSE_HEADERS = ['content-encoding', 'content-length', 'transfer-encoding'];
const TRACEPARENT = /^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$/;

let current = null;

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return Buffer.concat(chunks);
}

function isLoopback(address) {
  return address === '127.0.0.1' || address === '::1' || address === '::ffff:127.0.0.1';
}

async function setTraceparent(req, res) {
  try {
    const body = JSON.parse((await readBody(req)).toString('utf8'));
    if (typeof body.traceparent !== 'string' || !TRACEPARENT.test(body.traceparent)) throw new Error('bad');
    current = body.traceparent;
    res.writeHead(204);
    res.end();
  } catch (_) {
    res.writeHead(400);
    res.end('traceparent missing or malformed');
  }
}

async function forward(req, res) {
  if (!ROUTER_BASE) {
    res.writeHead(500);
    res.end('LLM_ROUTER_BASE_URL is unset');
    return;
  }
  const body = await readBody(req);
  const headers = { 'content-type': req.headers['content-type'] || 'application/json' };
  if (req.headers.authorization) headers.authorization = req.headers.authorization;
  if (req.headers.accept) headers.accept = req.headers.accept;
  if (current) headers.traceparent = current;
  try {
    const upstream = await fetch(ROUTER_BASE + req.url.slice(LLM_PREFIX.length), {
      method: req.method,
      headers,
      body: body.length ? body : undefined,
    });
    const out = {};
    for (const [name, value] of upstream.headers) {
      if (!DROP_RESPONSE_HEADERS.includes(name)) out[name] = value;
    }
    if (upstream.status >= 400) console.error(`[opencode-proxy] router ${upstream.status} for ${req.url}`);
    res.writeHead(upstream.status, out);
    if (upstream.body) Readable.fromWeb(upstream.body).pipe(res);
    else res.end();
  } catch (err) {
    res.writeHead(502);
    res.end(String(err));
  }
}

http
  .createServer((req, res) => {
    if (!isLoopback(req.socket.remoteAddress)) {
      res.writeHead(403);
      res.end('loopback only');
      return;
    }
    if (req.method === 'POST' && req.url === '/_traceparent') {
      setTraceparent(req, res);
      return;
    }
    if (req.url === LLM_PREFIX || req.url.startsWith(`${LLM_PREFIX}/`)) {
      forward(req, res);
      return;
    }
    res.writeHead(404);
    res.end();
  })
  .listen(PORT, '127.0.0.1', () => {
    console.log(`[opencode-proxy] 127.0.0.1:${PORT}${LLM_PREFIX} -> ${ROUTER_BASE || '<unset>'}`);
  });
