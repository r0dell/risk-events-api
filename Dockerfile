FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sources.py engine.py app.py paid_app.py ./
ENV PORT=8000
CMD ["sh", "-c", "uvicorn paid_app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
