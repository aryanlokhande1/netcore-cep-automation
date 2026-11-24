FROM python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39

ARG CHROMIUM_VERSION=156.0.8078.79-1~deb12u1
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates "chromium=${CHROMIUM_VERSION}" "chromium-sandbox=${CHROMIUM_VERSION}" \
        fonts-liberation xauth x11vnc xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.lock.txt .
RUN python -m pip install --no-cache-dir -r requirements.lock.txt && python -m pip check
COPY . .
RUN useradd --uid 10001 --create-home netcore \
    && install -d -o netcore -g netcore /app/outputs /app/profiles \
        /app/skills/login /app/skills/whatsapp /approved-login/approved

USER netcore
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium \
    NETCORE_SKILL_HISTORY_DIR=/approved-login
ENTRYPOINT ["python", "-m", "app.cli"]
