FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/chromium DISPLAY=:99
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m patchright install --with-deps chromium \
    && apt-get update \
    && apt-get install -y --no-install-recommends xvfb x11-utils x11vnc novnc websockify openbox tini \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 gateway \
    && mkdir -p /app/accounts /app/downloads /app/data \
    && chown -R gateway:gateway /app /opt/chromium
COPY --chown=gateway:gateway . .
RUN chmod +x deploy/entrypoint.sh
USER gateway
EXPOSE 8000 6080
ENTRYPOINT ["/usr/bin/tini", "--", "/app/deploy/entrypoint.sh"]
