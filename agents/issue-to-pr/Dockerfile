# issue-to-pr: a Python 3.12 A2A agent plus the OpenCode coding engine (Node), in one image.
#
# Python 3.12 is the base (a2a-sdk 1.1.x and langgraph are tested there); Node 20 is copied in from the
# official image, which is the runtime the opencode-agent proved on this platform. OpenCode is pinned to
# the opencode-agent's version and its first-run bootstrap (model catalog, ripgrep) is warmed at build time
# into /opt/opencode-warm, so the runtime /workspace HOME does not re-download it (opencode-agent
# DEBUGGING #7-10). The engine is started by the agent on first use.
FROM node:20-bookworm-slim AS node

FROM python:3.12-slim-bookworm

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version

RUN npm install -g opencode-ai@1.18.16 && npm cache clean --force

# The custom chat-completions provider (opencode-agent DEBUGGING #10: the built-in provider drops output).
COPY src/opencode.json /root/.config/opencode/opencode.json

# Warm OpenCode's first-run bootstrap once (copied approach from the opencode-agent Dockerfile): start the
# server, wait for the catalog and ripgrep, send one prompt so the provider's dependencies install (it fails
# with no API key, as expected), wait for the log to go quiet, stop the server, snapshot.
RUN set -eux; \
    export OPENCODE_DISABLE_DEFAULT_PLUGINS=1; \
    opencode serve --hostname 127.0.0.1 --port 4096 & srv=$!; \
    for i in $(seq 1 60); do \
      [ -s /root/.cache/opencode/models.json ] && [ -x /root/.cache/opencode/bin/rg ] && break; \
      sleep 1; \
    done; \
    node -e " \
      const http = require('http'); \
      function req(method, path, body) { \
        return new Promise((resolve, reject) => { \
          const data = body ? JSON.stringify(body) : undefined; \
          const r = http.request({hostname:'127.0.0.1', port:4096, path, method, timeout: 60000, headers: data ? {'content-type':'application/json','content-length':Buffer.byteLength(data)} : {}}, res => { \
            let d=''; res.on('data',c=>d+=c); res.on('end',()=>resolve({status:res.statusCode, body:d})); \
          }); \
          r.on('error', reject); r.on('timeout', () => r.destroy(new Error('timeout'))); \
          if (data) r.write(data); \
          r.end(); \
        }); \
      } \
      (async () => { \
        const s = await req('POST', '/session', {}); \
        const sid = JSON.parse(s.body).id; \
        const p = await req('POST', '/session/'+sid+'/message', {parts: [{type:'text', text:'hi'}], model: {providerID:'openai-chat', modelID:'gpt-4o-mini'}}); \
        console.log('warm-up prompt:', p.status); \
      })().catch(e => { console.log('warm-up prompt failed (expected, no API key):', e.message); }); \
    " || true; \
    log=/root/.local/share/opencode/log/opencode.log; prev=-1; stable=0; \
    for i in $(seq 1 90); do \
      sleep 1; \
      cur=$(wc -c < "$log" 2>/dev/null || echo 0); \
      if [ "$cur" = "$prev" ]; then stable=$((stable + 1)); else stable=0; fi; \
      [ "$stable" -ge 3 ] && break; \
      prev=$cur; \
    done; \
    kill "$srv" 2>/dev/null || true; \
    for i in $(seq 1 10); do kill -0 "$srv" 2>/dev/null || break; sleep 1; done; \
    kill -9 "$srv" 2>/dev/null || true; \
    wait "$srv" 2>/dev/null || true; \
    rm -rf /root/.local/state/opencode/locks; \
    mkdir -p /opt/opencode-warm; \
    cp -r /root/.cache /root/.config /root/.local /opt/opencode-warm/ 2>/dev/null || true; \
    chmod -R a+rX /opt/opencode-warm

RUN python -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

COPY src/ /app
RUN chmod +x /app/entrypoint.sh \
    && useradd --create-home --uid 10001 agent \
    && mkdir -p /workspace && chown agent /workspace
USER agent

ENV PATH="/opt/venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV HOME=/workspace
EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
