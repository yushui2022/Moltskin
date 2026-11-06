FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MOLTSKIN_DATA_DIR=/app/data \
    MOLTSKIN_HEADLESS=1 \
    MOLTSKIN_OPEN_MANAGER=0 \
    MOLTSKIN_HOST=0.0.0.0

WORKDIR /app

# CJK fonts for the UI and pages rendered inside the browser
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies and the Camoufox browser binary
COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt \
    && camoufox fetch

COPY . /app

EXPOSE 8765
VOLUME ["/app/data"]

CMD ["python", "/app/run.py"]
