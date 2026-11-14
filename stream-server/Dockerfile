# syntax=docker/dockerfile:1
# Zotero stream server (https://github.com/zotero/stream-server): WebSocket
# push notifications for instant sync and the browser-login handshake.
FROM node:22-alpine

ARG STREAM_SERVER_REPO=https://github.com/zotero/stream-server.git
ARG STREAM_SERVER_REF=master

RUN apk add --no-cache git
WORKDIR /usr/src/app
RUN git clone --depth 1 --branch "$STREAM_SERVER_REF" "$STREAM_SERVER_REPO" . \
    && npm ci --omit=dev \
    && rm -rf .git \
    && chown -R node:node /usr/src/app

USER node
EXPOSE 8080
# Configuration via NODE_CONFIG (JSON), see docker-compose.yml
CMD ["node", "index.js"]
