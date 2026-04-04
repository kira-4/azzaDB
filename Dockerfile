FROM python:3.12-slim

# gcc is required to compile TgCrypto (C extension bundled with hydrogram[fast])
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libssl-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

COPY . .

# Pre-create runtime directories so volume mounts have a target even on first run
RUN mkdir -p data downloads/audio downloads/covers

CMD ["python", "run_bot.py"]
