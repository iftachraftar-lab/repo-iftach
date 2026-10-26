FROM python:3.12-slim

WORKDIR /app

RUN groupadd -r appgroup && useradd -r -g appgroup -s /sbin/nologin appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY static/ ./static/
COPY bot_server.py .

RUN chown -R appuser:appgroup /app
USER appuser

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "bot_server:app", "--host", "0.0.0.0", "--port", "8080"]