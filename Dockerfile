FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

RUN mkdir -p /data
ENV SQLITE_PATH=/data/seen.db
ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "app.main"]
