FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# The static analyser runs as a subprocess from its own virtualenv, pinned, so its own pinned
# dependencies never change the agent's. It only ever reads the bundled rules under /app/rules.
ARG SCANNER_VERSION=1.181.0
RUN python -m venv /opt/scanner \
    && /opt/scanner/bin/pip install --no-cache-dir "semgrep==${SCANNER_VERSION}" \
    && /opt/scanner/bin/semgrep --version

COPY src/ /app
RUN useradd --create-home --uid 10001 agent
USER agent

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV SEMGREP_BIN=/opt/scanner/bin/semgrep
ENV SEMGREP_SEND_METRICS=off
ENV SEMGREP_ENABLE_VERSION_CHECK=0
EXPOSE 8000

CMD ["python", "__main__.py", "--host", "0.0.0.0", "--port", "8000"]
