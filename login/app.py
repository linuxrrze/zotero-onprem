"""
zotero-login -- minimal replacement for the zotero.org login page.

Since Zotero 9 the desktop client links an account like this:

  1. client  -> POST {api}/keys/sessions            -> {sessionToken, loginURL}
  2. client opens loginURL ({WWW_BASE_URI}login?session=TOKEN) in the browser
  3. the *website* authenticates the user and calls (as API super-user)
                POST {api}/keys/sessions/complete  {sessionToken, userID, access}
  4. the dataserver creates an API key and notifies the client
     (stream server "loginComplete" event, or the client polls the session)

The zotero.org website is not open source, so this small app implements
step 3 for an on-premise installation. Users are authenticated against the
zotero_www.users table (local passwords) and/or an LDAP directory.
"""
import logging
import os
import secrets
import time
from collections import defaultdict, deque

from flask import Flask, abort, render_template, request, session

import common
import ldap_auth

log = logging.getLogger("zotero-login")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = Flask(__name__)
app.secret_key = common.env("LOGIN_SECRET_KEY", required=True)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=common.env("LOGIN_COOKIE_SECURE", "1") == "1",
)

SITE_NAME = common.env("LOGIN_SITE_NAME", "Zotero (on-premise)")
AUTH_BACKENDS = [b.strip() for b in common.env("LOGIN_AUTH_BACKENDS", "local").split(",") if b.strip()]

# Simple per-IP throttle for failed logins
MAX_FAILURES = int(common.env("LOGIN_MAX_FAILURES", "10"))
FAILURE_WINDOW = int(common.env("LOGIN_FAILURE_WINDOW", "600"))
_failures = defaultdict(deque)


def _client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()


def _throttled(ip):
    q = _failures[ip]
    now = time.time()
    while q and q[0] < now - FAILURE_WINDOW:
        q.popleft()
    return len(q) >= MAX_FAILURES


def _csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _check_csrf():
    sent = request.form.get("csrf", "")
    if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
        abort(400, "Invalid form token -- please reload the page")


def _session_info(token):
    """Session status from the dataserver, or None if unknown/expired."""
    r = common.api("GET", f"keys/sessions/{token}/info")
    if r.status_code in (404, 410):
        return None
    r.raise_for_status()
    return r.json()


def _authenticate(login, password):
    """Return the zotero_www user row, or None."""
    conn = common.db()
    try:
        user = common.find_user(conn, login)
        if user and user["role"] == "deleted":
            return None

        if "local" in AUTH_BACKENDS and user and user["authSource"] == "local":
            if common.verify_password(password, user["password"]):
                return user

        if "ldap" in AUTH_BACKENDS and (user is None or user["authSource"] == "ldap"):
            entry = ldap_auth.authenticate(login, password)
            if entry:
                if user is None:
                    # Auto-provision directory users on first login. The
                    # dataserver creates its own records when the login
                    # session is completed (Zotero_Users::addFromWWW).
                    common.create_user(conn, entry["username"], entry["email"], auth_source="ldap")
                    log.info("provisioned LDAP user %s", entry["username"])
                    user = common.find_user(conn, entry["username"])
                return user
        return None
    finally:
        conn.close()


@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/login")
def login_form():
    token = request.args.get("session", "")
    if not token:
        return render_template("message.html", site=SITE_NAME, title="Zotero login",
                               message="Start the login from the Zotero desktop app "
                                       "(Settings → Sync → Log In)."), 400
    info = _session_info(token)
    if not info or info.get("status") != "pending":
        return render_template("message.html", site=SITE_NAME, title="Login link expired",
                               message="This login link is no longer valid. "
                                       "Please start the login again in Zotero."), 410
    return render_template("login.html", site=SITE_NAME, token=token, csrf=_csrf_token(),
                           error=None, client=info.get("clientType"))


@app.post("/login")
def login_submit():
    _check_csrf()
    token = request.form.get("session", "")
    login = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    ip = _client_ip()

    def form_error(msg, code=200):
        return render_template("login.html", site=SITE_NAME, token=token, csrf=_csrf_token(),
                               error=msg, client=None, username=login), code

    if _throttled(ip):
        return form_error("Too many failed attempts. Please wait a few minutes.", 429)

    info = _session_info(token)
    if not info or info.get("status") != "pending":
        return render_template("message.html", site=SITE_NAME, title="Login link expired",
                               message="This login link is no longer valid. "
                                       "Please start the login again in Zotero."), 410

    user = _authenticate(login, password) if login and password else None
    if not user:
        _failures[ip].append(time.time())
        log.warning("failed login for %r from %s", login, ip)
        return form_error("Invalid username or password.")

    # A client with an existing local library passes its userID -- the
    # dataserver then ignores ours, but refuse a mismatch for clarity.
    if info.get("userID") and int(info["userID"]) != int(user["userID"]):
        return form_error("This Zotero installation is linked to a different account.")

    r = common.api("POST", "keys/sessions/complete", json={
        "sessionToken": token,
        "userID": int(user["userID"]),
        "access": common.CLIENT_KEY_ACCESS,
    })
    if r.status_code != 204:
        log.error("completing session failed: %s %s", r.status_code, r.text[:500])
        return form_error("The server could not complete the login (HTTP %d)." % r.status_code, 502)

    log.info("login completed for %s (userID %s) from %s", user["username"], user["userID"], ip)
    session.pop("csrf", None)
    return render_template("message.html", site=SITE_NAME, title="Logged in",
                           message=f"You are logged in as {user['username']}. "
                                   "You can close this window and return to Zotero.")


@app.post("/login/cancel")
def login_cancel():
    _check_csrf()
    token = request.form.get("session", "")
    if token:
        # Unauthenticated endpoint, same call the client makes
        common.api("DELETE", f"keys/sessions/{token}")
    return render_template("message.html", site=SITE_NAME, title="Login cancelled",
                           message="The login was cancelled. You can close this window.")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
