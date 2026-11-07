FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py /app/main.py

ENV PYTHONUNBUFFERED=1

CMD ["sh", "-c", "if [ -n \"$TLS_CERTFILE\" ] && [ -n \"$TLS_KEYFILE\" ]; then exec uvicorn main:app --host 0.0.0.0 --port 8000 --ssl-certfile \"$TLS_CERTFILE\" --ssl-keyfile \"$TLS_KEYFILE\"; else exec uvicorn main:app --host 0.0.0.0 --port 8000; fi"]
