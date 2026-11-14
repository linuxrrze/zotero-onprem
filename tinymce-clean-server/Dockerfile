# syntax=docker/dockerfile:1
# Zotero note HTML sanitiser (https://github.com/zotero/tinymce-clean-server).
# The dataserver posts every note to it; it is required.
FROM node:22-alpine

ARG TINYMCE_CLEAN_REPO=https://github.com/zotero/tinymce-clean-server.git
ARG TINYMCE_CLEAN_REF=master

RUN apk add --no-cache git
WORKDIR /usr/src/app
RUN git clone --depth 1 --branch "$TINYMCE_CLEAN_REF" "$TINYMCE_CLEAN_REPO" . \
    && npm install --omit=dev \
    && rm -rf .git \
    && chown -R node:node /usr/src/app

USER node
EXPOSE 16342
ENV TINYMCE_CLEAN_HOST=0.0.0.0 TINYMCE_CLEAN_PORT=16342
CMD ["node", "server.js"]
