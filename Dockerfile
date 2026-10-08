FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY tailcam ./tailcam

ENV PYTHONUNBUFFERED=1
EXPOSE 8080
# Traefik only routes to a healthy container, so check every 2s while starting up instead of
# waiting 30s for the first check. --start-interval needs Docker 25+ to build (CI's buildx is fine)
# and to take effect at runtime; older engines running the image just check every 30s from the start.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --start-interval=2s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
CMD ["python", "-m", "tailcam"]
