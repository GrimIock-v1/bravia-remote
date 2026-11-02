FROM python:3.12-alpine

LABEL org.opencontainers.image.title="Bravia Remote" \
      org.opencontainers.image.description="Phone-friendly web remote for older (non-Android) Sony BRAVIA TVs" \
      org.opencontainers.image.source="https://github.com/GrimIock-v1/bravia-remote" \
      org.opencontainers.image.licenses="MIT"

RUN apk add --no-cache su-exec

ENV PYTHONUNBUFFERED=1 \
    CONFIG_DIR=/config \
    PORT=8787 \
    PUID=99 \
    PGID=100 \
    TV_IP=10.0.0.61 \
    TV_MAC=00:71:cc:4b:a0:63

WORKDIR /app
COPY app/tv_remote.py /app/tv_remote.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /config

VOLUME /config
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python3 -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8787')+'/healthz',timeout=4)" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "/app/tv_remote.py"]
