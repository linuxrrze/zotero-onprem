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
step 3 for an on-premise installation. Backends (LOGIN_AUTH_BACKENDS):

  local  passwords in zotero_www.users (zotadm user add/passwd)
  ldap   LDAP / Active Directory bind (ldap_auth.py)
  oidc   OpenID Connect single sign-on (oidc_auth.py), with an explicit
         confirmation step before the client gets its key
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
PASSWORD_LOGIN = any(b in AUTH_BACKENDS for b in ("local", "ldap"))
OIDC_LOGIN = "oidc" in AUTH_BACKENDS
# Public base URL of this service (for the OIDC redirect URI)
BASE_URL = (common.env("LOGIN_BASE_URL", "") or "").rstrip("/") + "/"
# How long a confirmation page stays valid after returning from the IdP
CONFIRM_TTL = 600

if OIDC_LOGIN:
    import oidc_auth
    if BASE_URL == "/":
        raise RuntimeError("LOGIN_BASE_URL must be set for OIDC (e.g. https://zotero.example.org/)")
    oidc_auth.init_app(app)

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
    if not token:
        return None
    r = common.api("GET", f"keys/sessions/{token}/info")
    if r.status_code in (404, 410):
        return None
    r.raise_for_status()
    return r.json()


def _pending(token):
    info = _session_info(token)
    return info if info and info.get("status") == "pending" else None


def _message(title, message, code=200):
    return render_template("message.html", site=SITE_NAME, title=title, message=message), code


def _expired():
    return _message("Login link expired", "This login link is no longer valid. "
                    "Please start the login again in Zotero.", 410)


def _login_page(token, info, error=None, username="", code=200):
    return render_template(
        "login.html", site=SITE_NAME, token=token, csrf=_csrf_token(), error=error,
        username=username, client=(info or {}).get("clientType"),
        password_login=PASSWORD_LOGIN, oidc_login=OIDC_LOGIN,
        oidc_label=oidc_auth.BUTTON_LABEL if OIDC_LOGIN else ""), code


def _authenticate(login, password):
    """Password backends: return the zotero_www user row, or None."""
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
                # Use the account the directory authenticated, not whatever the
                # typed login string matched (e.g. an e-mail address).
                dir_user = common.find_user(conn, entry["username"])
                if dir_user is None:
                    if user is not None:
                        log.warning("LDAP login %r: directory user %r differs from %r",
                                    login, entry["username"], user["username"])
                        return None
                    # Auto-provision directory users on first login. The
                    # dataserver creates its own records when the login
                    # session is completed (Zotero_Users::addFromWWW).
                    common.create_user(conn, entry["username"], entry["email"], auth_source="ldap")
                    log.info("provisioned LDAP user %s", entry["username"])
                    return common.find_user(conn, entry["username"])
                if dir_user["authSource"] != "ldap" or dir_user["role"] == "deleted":
                    log.warning("LDAP login %r: account %r is not an active LDAP account",
                                login, entry["username"])
                    return None
                if user is not None and user["userID"] != dir_user["userID"]:
                    return None
                return dir_user
        return None
    finally:
        conn.close()


def _complete(token, info, user):
    """Hand the session over to the dataserver, which creates the API key.
    Returns None on success or an error message."""
    # A client with an existing local library passes its userID, and the
    # dataserver then ignores ours. Refusing a mismatch is security-critical:
    # otherwise anyone could start a session claiming someone else's userID.
    if info.get("userID") and int(info["userID"]) != int(user["userID"]):
        return "This Zotero installation is linked to a different account."
    r = common.api("POST", "keys/sessions/complete", json={
        "sessionToken": token,
        "userID": int(user["userID"]),
        "access": common.CLIENT_KEY_ACCESS,
    })
    if r.status_code != 204:
        log.error("completing session failed: %s %s", r.status_code, r.text[:500])
        return "The server could not complete the login (HTTP %d)." % r.status_code
    log.info("login completed for %s (userID %s) from %s", user["username"], user["userID"], _client_ip())
    return None


def _logged_in(user):
    session.pop("csrf", None)
    return _message("Logged in", f"You are logged in as {user['username']}. "
                    "You can close this window and return to Zotero.")


@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/login")
def login_form():
    token = request.args.get("session", "")
    if not token:
        return _message("Zotero login", "Start the login from the Zotero desktop app "
                        "(Settings → Sync → Log In).", 400)
    info = _pending(token)
    if not info:
        return _expired()
    return _login_page(token, info)


@app.post("/login")
def login_submit():
    _check_csrf()
    if not PASSWORD_LOGIN:
        abort(404)
    token = request.form.get("session", "")
    login = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    ip = _client_ip()

    if _throttled(ip):
        return _login_page(token, None, "Too many failed attempts. Please wait a few minutes.",
                           login, 429)

    info = _pending(token)
    if not info:
        return _expired()

    user = _authenticate(login, password) if login and password else None
    if not user:
        _failures[ip].append(time.time())
        log.warning("failed login for %r from %s", login, ip)
        return _login_page(token, info, "Invalid username or password.", login)

    error = _complete(token, info, user)
    if error:
        return _login_page(token, info, error, login, 502 if "HTTP" in error else 200)
    return _logged_in(user)


@app.get("/login/oidc")
def oidc_start():
    if not OIDC_LOGIN:
        abort(404)
    token = request.args.get("session", "")
    if not _pending(token):
        return _expired()
    session.pop("oidc_confirm", None)
    session["oidc_session"] = token
    return oidc_auth.authorize_redirect(BASE_URL + "login/oidc/callback")


@app.get("/login/oidc/callback")
def oidc_callback():
    if not OIDC_LOGIN:
        abort(404)
    token = session.pop("oidc_session", None)
    if not token:
        return _message("Login failed", "Your browser session expired. "
                        "Please start the login again in Zotero.", 400)
    if request.args.get("error"):
        log.warning("IdP returned error %s: %s", request.args.get("error"),
                    request.args.get("error_description", ""))
        return _message("Login cancelled", "The login at your identity provider was not "
                        "completed. You can close this window.", 400)
    try:
        claims = oidc_auth.fetch_claims()
    except Exception as e:  # invalid state/token, IdP unreachable, ...
        log.warning("OIDC login failed: %s", e)
        return _message("Login failed", "The response from the identity provider could not "
                        "be verified. Please start the login again in Zotero.", 400)

    if not oidc_auth.is_allowed(claims):
        log.warning("OIDC login refused for %s: %s=%s", oidc_auth.external_id(claims),
                    oidc_auth.ALLOWED_CLAIM, claims.get(oidc_auth.ALLOWED_CLAIM))
        return _message("Access denied", "Your account is not permitted to use this "
                        "Zotero server.", 403)

    user = oidc_auth.user_for(claims)
    if user["role"] == "deleted":
        log.warning("OIDC login refused for disabled user %s", user["username"])
        return _message("Access denied", "This account has been disabled.", 403)

    info = _pending(token)
    if not info:
        return _expired()

    # Explicit confirmation: with single sign-on a forwarded login link would
    # otherwise hand out a key with a single click.
    session["oidc_confirm"] = {"token": token, "userID": int(user["userID"]), "ts": int(time.time())}
    return render_template("confirm.html", site=SITE_NAME, csrf=_csrf_token(), token=token,
                           username=user["username"], email=user["email"],
                           client=info.get("clientType"))


@app.post("/login/confirm")
def oidc_confirm():
    _check_csrf()
    pending = session.pop("oidc_confirm", None)
    token = request.form.get("session", "")
    if (not pending or pending["token"] != token
            or time.time() - pending["ts"] > CONFIRM_TTL):
        return _message("Login failed", "This confirmation is no longer valid. "
                        "Please start the login again in Zotero.", 400)
    info = _pending(token)
    if not info:
        return _expired()
    conn = common.db()
    try:
        user = common.get_user(conn, pending["userID"])
    finally:
        conn.close()
    if not user or user["role"] == "deleted":
        return _message("Access denied", "This account has been disabled.", 403)
    error = _complete(token, info, user)
    if error:
        return _message("Login failed", error, 502 if "HTTP" in error else 403)
    return _logged_in(user)


@app.post("/login/cancel")
def login_cancel():
    _check_csrf()
    token = request.form.get("session", "")
    session.pop("oidc_confirm", None)
    if token:
        # Unauthenticated endpoint, same call the client makes
        common.api("DELETE", f"keys/sessions/{token}")
    return _message("Login cancelled", "The login was cancelled. You can close this window.")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
