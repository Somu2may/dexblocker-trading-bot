FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8080 TZ=Asia/Kolkata
RUN apt-get update && apt-get install -y --no-install-recommends curl procps tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN chmod +x start.sh && mkdir -p /app/bot/data
VOLUME ["/app/bot/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fs http://127.0.0.1:${PORT}/healthz || exit 1
CMD ["./start.sh"]
