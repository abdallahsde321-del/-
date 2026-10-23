FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir "urllib3<2.0.0" -r requirements.txt && \
    pip install --no-cache-dir --upgrade streamlink

CMD ["python", "main.py"]
