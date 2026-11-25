FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    APP_ENV=production COOKIE_SECURE=1 INSTANCE_DIR=/data PORT=3000
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock \
    && groupadd --gid 10001 tourism \
    && useradd --uid 10001 --gid tourism --no-create-home tourism \
    && mkdir -p /data && chown tourism:tourism /data && chmod 700 /data
COPY --chown=tourism:tourism app.py gunicorn.conf.py ./
COPY --chown=tourism:tourism templates ./templates
COPY --chown=tourism:tourism static ./static
USER tourism
EXPOSE 3000
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]
