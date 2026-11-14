# syntax=docker/dockerfile:1
# zotero-login: replacement for the zotero.org login page (browser-based
# account linking used by Zotero 9+), plus the zotadm admin CLI.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -r -u 10001 app
USER app
EXPOSE 8000
# Access log without query string or referer: both carry the login session
# token (/login?session=...), which can be exchanged for the new API key.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-", \
     "--access-logformat", "%(h)s %(l)s %(u)s %(t)s \"%(m)s %(U)s %(H)s\" %(s)s %(b)s \"%(a)s\"", \
     "app:app"]
