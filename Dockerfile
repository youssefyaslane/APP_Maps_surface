FROM python:3.12-slim

WORKDIR /app

# Dépendances système requises par opencv-python-headless
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

ENV FLASK_APP=app.py
ENV CACHE_DIR=/app/cache
EXPOSE 5000

CMD ["python", "-m", "flask", "run", "--host=0.0.0.0", "--no-debugger"]
