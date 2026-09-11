FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY wsgi.py .
COPY app ./app

ENV DATABASE_PATH=/data/food.db
RUN mkdir -p /data

CMD ["gunicorn", "-w", "1", "--threads", "4", "-b", "0.0.0.0:8000", "wsgi:app"]
