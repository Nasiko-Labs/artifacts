#!/bin/bash
# Prepares the writable HOME for OpenCode and starts the Python A2A server on :8000. OpenCode itself
# (and its loopback LLM proxy) are started by the agent on the first engine run, not here, so an idle
# agent holds no Node memory and a dead engine can be restarted. Never fails the container: without a
# writable /workspace (no --writable) the agent still answers and says the engine can't run.
export HOME="${HOME:-/workspace}"
if mkdir -p "$HOME/.config/opencode" 2>/dev/null; then
  cp -r /opt/opencode-warm/. "$HOME/" 2>/dev/null || echo "entrypoint: could not copy the warmed OpenCode cache" >&2
  cp /app/opencode.json "$HOME/.config/opencode/opencode.json" 2>/dev/null || true
  rm -rf "$HOME/.local/state/opencode/locks" 2>/dev/null || true
else
  echo "entrypoint: $HOME is not writable; the coding engine will be unavailable (deploy with --writable)" >&2
fi
export OPENCODE_DISABLE_DEFAULT_PLUGINS=1
cd /app
exec /opt/venv/bin/python __main__.py --host 0.0.0.0 --port 8000
