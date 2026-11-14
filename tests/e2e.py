#!/usr/bin/env python3
"""
End-to-end test of a running stack, following the same protocol steps as the
Zotero desktop client:

  1. browser login   POST /keys/sessions -> login page (form) -> session completed -> API key
  2. metadata sync   create an attachment item through the Web API
  3. file sync       authorise upload -> POST to S3 (presigned policy) -> register -> download
  4. push            WebSocket connect to the stream server

  5. account admin   legacy POST /keys blocked; with E2E_OIDC=1: single sign-on via
                     the mock IdP, confirmation step, stable account mapping, and
                     (E2E_ZOTADM=./bin/zotadm) disabled accounts are locked out

Usage: ZOTERO_HOST=zotero.test E2E_USER=alice E2E_PASSWORD=... python tests/e2e.py
Requires: pip install requests websockets
"""
import asyncio
import hashlib
import json
import os
import re
import ssl
import sys
import time

import requests
import urllib3

urllib3.disable_warnings()

HOST = os.environ["ZOTERO_HOST"]
USER = os.environ.get("E2E_USER", "alice")
PASSWORD = os.environ["E2E_PASSWORD"]
VERIFY = os.environ.get("E2E_VERIFY_TLS", "0") == "1"
API = f"https://{HOST}/"
H = {"Zotero-API-Version": "3", "User-Agent": "Zotero/10.0 (e2e test)"}


def step(msg):
    print(f"\n== {msg}", flush=True)


def check(cond, msg, resp=None):
    if not cond:
        detail = f"\n   HTTP {resp.status_code}: {resp.text[:800]}" if resp is not None else ""
        print(f"FAIL: {msg}{detail}", flush=True)
        sys.exit(1)
    print(f"   ok: {msg}", flush=True)


def main():
    s = requests.Session()
    s.verify = VERIFY

    step("API reachable")
    r = s.get(API + "itemTypes", headers=H)
    check(r.status_code == 200 and "book" in r.text, "GET /itemTypes", r)

    step("Proxy blocks super-user access from outside")
    # The dataserver would answer these with 403 too (wrong/no credentials), so
    # require the proxy's own response: the request must never reach it.
    def blocked(r):
        return r.status_code == 403 and r.text.strip() == "Blocked by proxy"

    r = s.get(API + "keys/sessions/x/info", headers=H)
    check(blocked(r), "internal endpoint blocked by proxy", r)
    r = s.get(API + "Keys/Sessions/x/info/", headers=H)
    check(blocked(r), "case / trailing-slash variant blocked by proxy", r)
    r = s.get(API + "users/1/keys", headers=H, auth=("x", "y"))
    check(blocked(r), "HTTP Basic auth blocked by proxy", r)
    r = s.post(API + "keys", headers=H, json={"username": USER, "password": PASSWORD,
                                              "name": "x", "access": {"user": {"library": True}}})
    check(blocked(r), "legacy password key creation (POST /keys) blocked by proxy", r)

    step("Login session (what Zotero ≥ 9 does on 'Log In')")
    token, browser, r = start_login(s)
    check('name="password"' in r.text, "login page shows password form", r)
    csrf = csrf_of(r)

    r = browser.post(f"https://{HOST}/login", data={
        "csrf": csrf, "session": token, "username": USER, "password": "wrong-password"})
    check("Invalid username or password" in r.text, "wrong password rejected", r)

    r = browser.post(f"https://{HOST}/login", data={
        "csrf": csrf, "session": token, "username": USER, "password": PASSWORD})
    check(r.status_code == 200 and "You are logged in" in r.text, "login form accepted", r)

    key, user_id = session_key(s, token)
    A = dict(H, **{"Zotero-API-Key": key})

    r = s.get(API + "keys/current", headers=A)
    check(r.status_code == 200 and r.json()["access"]["user"].get("files"), "key has file access", r)

    step("Metadata: create attachment item")
    item = [{"itemType": "attachment", "linkMode": "imported_file", "title": "e2e.txt",
             "contentType": "text/plain", "charset": "utf-8", "filename": "e2e.txt",
             "tags": [], "relations": {}}]
    r = s.post(API + f"users/{user_id}/items", headers=A, json=item)
    check(r.status_code == 200 and r.json().get("successful"), "POST items", r)
    item_key = r.json()["successful"]["0"]["key"]

    step("File sync: upload via S3 presigned POST policy")
    content = f"Zotero on-premise e2e test {time.time()}\n".encode() * 50
    md5 = hashlib.md5(content).hexdigest()
    file_url = API + f"users/{user_id}/items/{item_key}/file"
    r = s.post(file_url, headers=dict(A, **{"If-None-Match": "*"}), data={
        "md5": md5, "filename": "e2e.txt", "filesize": len(content),
        "mtime": int(time.time() * 1000)})
    check(r.status_code == 200 and "uploadKey" in r.json(), "upload authorised", r)
    auth = r.json()
    check(auth["url"].startswith("https://"), f"upload URL = {auth['url']}")

    body = auth["prefix"].encode() + content + auth["suffix"].encode()
    r = requests.post(auth["url"], data=body, headers={"Content-Type": auth["contentType"]},
                      verify=VERIFY)
    check(r.status_code == 201, "POST file to object storage (expects 201)", r)

    r = s.post(file_url, headers=dict(A, **{"If-None-Match": "*"}), data={"upload": auth["uploadKey"]})
    check(r.status_code == 204, "upload registered", r)

    r = s.get(file_url, headers=A, allow_redirects=False)
    check(r.status_code == 302, "download redirects to presigned URL", r)
    loc = r.headers["Location"]
    r = requests.get(loc, verify=VERIFY)
    check(r.status_code == 200 and r.content == content, f"downloaded file matches ({loc.split('?')[0]})", r)

    # Deduplication: another attachment with identical content needs no upload
    r = s.post(API + f"users/{user_id}/items", headers=A, json=item)
    check(r.status_code == 200 and r.json().get("successful"), "second attachment item", r)
    file_url2 = API + f"users/{user_id}/items/{r.json()['successful']['0']['key']}/file"
    r = s.post(file_url2, headers=dict(A, **{"If-None-Match": "*"}), data={
        "md5": md5, "filename": "e2e.txt", "filesize": len(content), "mtime": int(time.time() * 1000)})
    check(r.status_code == 200 and r.json().get("exists") == 1, "deduplication: existing file recognised", r)

    step("Push: stream server WebSocket")
    asyncio.run(stream_check(key))

    if os.environ.get("E2E_OIDC") == "1":
        oidc_checks(s, user_id)

    print("\nALL E2E CHECKS PASSED")


def start_login(s):
    """POST /keys/sessions like the client, open the login page like a browser."""
    r = s.post(API + "keys/sessions", headers=H)
    check(r.status_code == 201, "POST /keys/sessions", r)
    sess = r.json()
    token, login_url = sess["sessionToken"], sess["loginURL"]
    check(login_url.startswith(f"https://{HOST}/login?session="), f"loginURL = {login_url}")
    browser = requests.Session()
    browser.verify = VERIFY
    r = browser.get(login_url)
    check(r.status_code == 200 and "asks to be linked" in r.text, "login page renders (shows client)", r)
    return token, browser, r


def form_action(r):
    """URL the page's first form posts to, resolved like a browser would."""
    from urllib.parse import urljoin
    return urljoin(r.url, re.search(r'<form method="post" action="([^"]+)"', r.text).group(1))


def csrf_of(r):
    return re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)


def session_key(s, token):
    r = s.get(API + f"keys/sessions/{token}", headers=H)
    check(r.status_code == 200 and r.json().get("status") == "completed", "session completed", r)
    info = r.json()
    key, user_id = info["apiKey"], info["userID"]
    check(bool(key) and user_id, f"got API key for userID {user_id}")
    return key, user_id


def oidc_login(s):
    """Login via the identity provider; returns (session token, browser, response)."""
    token, browser, r = start_login(s)
    check('href="login/oidc?session=' in r.text, "login page offers single sign-on", r)
    # login/oidc -> IdP authorize -> back to login/oidc/callback -> confirmation page
    r = browser.get(f"https://{HOST}/login/oidc?session={token}")
    return token, browser, r


def oidc_checks(s, local_user_id):
    """Needs the mock IdP from tests/compose.ci.yml (user "bob") and, for the
    disable check, E2E_ZOTADM (e.g. ./bin/zotadm)."""
    step("OIDC single sign-on (mock identity provider)")
    token, browser, r = oidc_login(s)
    check(r.status_code == 200 and "Link Zotero to your account?" in r.text,
          "IdP round trip ends on the confirmation page", r)
    check("bob" in r.text, "confirmation names the account", r)
    r2 = s.get(API + f"keys/sessions/{token}", headers=H)
    check(r2.json().get("status") == "pending", "no key before the user confirms", r2)

    r = browser.post(form_action(r), data={"csrf": csrf_of(r), "session": token})
    check(r.status_code == 200 and "You are logged in as bob" in r.text, "confirmation accepted", r)
    key, user_id = session_key(s, token)
    check(user_id != local_user_id, "OIDC user is a separate account")
    B = dict(H, **{"Zotero-API-Key": key})
    r = s.get(API + f"users/{user_id}/items", headers=B)
    check(r.status_code == 200, "OIDC user's key works", r)

    step("OIDC: second login maps to the same account (issuer + sub)")
    token2, browser, r = oidc_login(s)
    r = browser.post(form_action(r), data={"csrf": csrf_of(r), "session": token2})
    check(r.status_code == 200 and "You are logged in as bob" in r.text, "second login", r)
    _, user_id2 = session_key(s, token2)
    check(user_id2 == user_id, f"same userID {user_id2}")

    zotadm = os.environ.get("E2E_ZOTADM")
    if not zotadm:
        return
    import subprocess

    step("Disabled account is locked out")
    out = subprocess.run([zotadm, "user", "disable", "bob"], capture_output=True, text=True)
    check(out.returncode == 0, f"zotadm user disable bob: {out.stdout.strip()} {out.stderr.strip()}")
    r = s.get(API + f"users/{user_id}/items", headers=B)
    check(r.status_code == 403, "existing key revoked", r)
    token3, browser, r = oidc_login(s)
    check(r.status_code == 403 and "disabled" in r.text, "login refused for disabled account", r)
    out = subprocess.run([zotadm, "user", "enable", "bob"], capture_output=True, text=True)
    check(out.returncode == 0, "zotadm user enable bob")


async def stream_check(key):
    import websockets
    ctx = ssl.create_default_context()
    if not VERIFY:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    async with websockets.connect(f"wss://{HOST}/stream/", ssl=ctx,
                                  additional_headers={"Zotero-API-Key": key}) as ws:
        msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
        check(msg.get("event") == "connected", f"stream server says {msg.get('event')}")
        if "topics" in msg:
            print(f"   topics: {msg['topics']}")


if __name__ == "__main__":
    main()
