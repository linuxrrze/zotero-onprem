#!/usr/bin/env python3
"""
End-to-end test of a running stack, following the same protocol steps as the
Zotero desktop client:

  1. browser login   POST /keys/sessions -> login page (form) -> session completed -> API key
  2. metadata sync   create an attachment item through the Web API
  3. file sync       authorise upload -> POST to S3 (presigned policy) -> register -> download
  4. push            WebSocket connect to the stream server

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
    r = s.get(API + "keys/sessions/x/info", headers=H)
    check(r.status_code == 403, "internal endpoint blocked", r)
    r = s.get(API + "users/1/keys", headers=H, auth=("x", "y"))
    check(r.status_code == 403, "HTTP Basic auth rejected", r)

    step("Login session (what Zotero ≥ 9 does on 'Log In')")
    r = s.post(API + "keys/sessions", headers=H)
    check(r.status_code == 201, "POST /keys/sessions", r)
    sess = r.json()
    token, login_url = sess["sessionToken"], sess["loginURL"]
    check(login_url.startswith(f"https://{HOST}/login?session="), f"loginURL = {login_url}")

    browser = requests.Session()
    browser.verify = VERIFY
    r = browser.get(login_url)
    check(r.status_code == 200 and 'name="password"' in r.text, "login page renders", r)
    csrf = re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)

    r = browser.post(f"https://{HOST}/login", data={
        "csrf": csrf, "session": token, "username": USER, "password": "wrong-password"})
    check("Invalid username or password" in r.text, "wrong password rejected", r)

    r = browser.post(f"https://{HOST}/login", data={
        "csrf": csrf, "session": token, "username": USER, "password": PASSWORD})
    check(r.status_code == 200 and "You are logged in" in r.text, "login form accepted", r)

    r = s.get(API + f"keys/sessions/{token}", headers=H)
    check(r.status_code == 200 and r.json().get("status") == "completed", "session completed", r)
    info = r.json()
    key, user_id = info["apiKey"], info["userID"]
    check(bool(key) and user_id, f"got API key for userID {user_id}")
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

    r = s.post(file_url, headers=dict(A, **{"If-None-Match": "*"}), data={
        "md5": md5, "filename": "e2e.txt", "filesize": len(content), "mtime": int(time.time() * 1000)})
    check(r.status_code == 200 and r.json().get("exists") == 1, "deduplication: existing file recognised", r)

    step("Push: stream server WebSocket")
    asyncio.run(stream_check(key))

    print("\nALL E2E CHECKS PASSED")


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
