FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY src/ /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# Root FS is read-only: CrewAI writes only under /tmp, and never phones home.
ENV HOME=/tmp
ENV CREWAI_STORAGE_DIR=/tmp/crewai
ENV CREWAI_DISABLE_TELEMETRY=true
ENV CREWAI_TRACING_ENABLED=false
ENV CREWAI_DISABLE_VERSION_CHECK=true
EXPOSE 8000

CMD ["python", "__main__.py", "--host", "0.0.0.0", "--port", "8000"]
