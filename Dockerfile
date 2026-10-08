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

# Navigateur automatique de la recherche gratuite du chatbot
# (agent_chatbot_workflow/outils/google_maps.py) : Playwright et Chromium avec
# ses bibliothèques système. Couche à part, après requirements.txt, pour ne pas
# réinstaller PyTorch à chaque changement ; version fixée ici plutôt que dans
# requirements.txt pour la même raison.
RUN pip install --no-cache-dir playwright==1.63.0 \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY . .

ENV FLASK_APP=app.py
ENV CACHE_DIR=/app/cache
EXPOSE 5000

CMD ["python", "-m", "flask", "run", "--host=0.0.0.0", "--no-debugger"]
