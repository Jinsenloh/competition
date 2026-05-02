FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    SUPPORT_COUNTER_DB=/tmp/agent_support_counter.db \
    SERVE_FRONTEND=false

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend

CMD ["sh", "-c", "uvicorn backend.server:app --host 0.0.0.0 --port ${PORT:-10000}"]
