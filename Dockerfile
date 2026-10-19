FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY web_app_dashboard/frontend/package*.json ./web_app_dashboard/frontend/
COPY web_app_dashboard/frontend/ ./web_app_dashboard/frontend/
COPY web_app_dashboard/backend/requirements.txt ./web_app_dashboard/backend/
COPY web_app_dashboard/backend/ ./web_app_dashboard/backend/
COPY bot_server.py .

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "web_app_dashboard.backend.main:app", "--host", "0.0.0.0", "--port", "8080"]