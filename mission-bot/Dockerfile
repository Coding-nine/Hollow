FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user; /data is where the JSON files live (mount a volume here).
RUN useradd --create-home bot && mkdir -p /data && chown -R bot /data /app
USER bot
VOLUME /data

CMD ["python", "bot.py"]
