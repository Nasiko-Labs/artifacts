FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY AgentCard.json .

ENV PORT=8000
ENV PYTHONUNBUFFERED=1
EXPOSE $PORT

CMD ["python", "src/__main__.py", "--host", "0.0.0.0", "--port", "8000"]
