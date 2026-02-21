FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY bot.py /app/bot.py
COPY digest.py /app/digest.py
COPY daily_source_digest.py /app/daily_source_digest.py

CMD ["python", "-u", "bot.py"]
