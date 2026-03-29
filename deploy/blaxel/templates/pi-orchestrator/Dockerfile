FROM node:22-alpine

WORKDIR /workspace

COPY --from=ghcr.io/blaxel-ai/sandbox:latest /sandbox-api /usr/local/bin/sandbox-api

RUN apk update && apk add --no-cache \
  bash \
  curl \
  git \
  netcat-openbsd \
  python3 \
  py3-pip \
  && npm install -g @mariozechner/pi-coding-agent \
  && rm -rf /var/cache/apk/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PATH="/usr/local/bin:$PATH"

ENTRYPOINT ["/entrypoint.sh"]
