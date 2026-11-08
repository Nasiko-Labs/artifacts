FROM python:3.12-slim

WORKDIR /app

# requirements.txt pins pytest: the sandboxed test run sees only the standard library and pytest.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY src/ /app
RUN useradd --create-home --uid 10001 agent \
    && mkdir -p /workspace && chown agent /workspace
USER agent

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000

CMD ["python", "__main__.py", "--host", "0.0.0.0", "--port", "8000"]
