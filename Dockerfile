FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY static/ ./static/
COPY bot_server.py .

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "bot_server:app", "--host", "0.0.0.0", "--port", "8080"]