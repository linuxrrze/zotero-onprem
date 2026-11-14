# Running Zotero fully on-premise: investigation results

*Status: November 2026. Upstream checked: `zotero/zotero` (client, release 10.0.8),
`zotero/dataserver` @ `bdc2dc7` (2026-11-12), `zotero/stream-server`,
`zotero/tinymce-clean-server`, plus the community projects
[uniuuu/zotprime](https://github.com/uniuuu/zotprime) and
[foxsen/zotero-selfhost](https://github.com/foxsen/zotero-selfhost).*

## TL;DR

* **It works, and the clients don't need to be rebuilt.** The official desktop
  client reads two hidden preferences, `extensions.zotero.api.url` and
  `extensions.zotero.streaming.url`. Set them with a profile `user.js` or with
  system-wide, locked AutoConfig. The unmodified client then syncs against your
  own server, and automatic updates keep working.
* **The hard part changed with Zotero 9.** The client no longer sends a
  username and password. It opens a login page on the **closed-source
  zotero.org website**. Existing self-host projects (ZotPrime) predate this
  change and only work with Zotero ≤ 8 clients. This repository adds a small
  open replacement for that login page (`login/`).
* **The server side is all open source (AGPL).** The dataserver needs two small
  patches: S3-compatible storage and an optional AWS-only full-text component.
  Every other AWS dependency is either unused or can be configured away.
* **The usual building blocks are gone.** MinIO (Docker images stopped in
  October 2025, repository archived in 2026) and LocalStack (account/token
  required since March 2026) are no longer good choices. This setup uses
  **Garage or RustFS** for S3 (selectable) and doesn't need an AWS emulator at
  all.

## 1. Components

| Component | Source | Needed | Role |
|---|---|---|---|
| dataserver (PHP) | zotero/dataserver | yes | Zotero Web API v3: sync, groups, keys, file-upload authorisation |
| MySQL **8** | – | yes | master, 2 shards, ids, `zotero_www` (users) |
| Redis | – | yes | rate limiting, notifications to the stream server |
| Memcached | – | yes | caching |
| S3-compatible object store | Garage (or Ceph RGW, SeaweedFS, …) | yes | attachments + full-text content; **clients upload and download directly** via presigned URLs |
| tinymce-clean-server (Node) | zotero/tinymce-clean-server | yes | sanitises note HTML on every note write |
| stream-server (Node) | zotero/stream-server | strongly recommended | WebSocket push: instant sync, `loginComplete` event |
| **login page** | *zotero.org website (closed)* → `login/` here | yes for Zotero ≥ 9 | authenticates users and completes login sessions |
| Elasticsearch 7 | – | optional | only Web-API full-text search (`qmode=everything`) |
| translation-server, citeproc | zotero/* | no | only used by zotero.org web features; the desktop client runs translators locally |

## 2. Client side: what actually needs to change

`resource/config.mjs` hard-codes `api.zotero.org` and similar URLs. That's why
earlier guides (ZotPrime, zotero-selfhost) rebuild the client or patch
`omni.ja`. But the code that matters prefers preferences:

| Function | Code | Override |
|---|---|---|
| Sync API base URL | `xpcom/sync/syncRunner.js:50` | `extensions.zotero.api.url` |
| Streaming (WebSocket) URL | `xpcom/streamer.js:247` | `extensions.zotero.streaming.url` |
| Login (`POST /keys/sessions`) | `syncAPIClient.js` → `baseURL` | follows `api.url`; **the browser login URL comes from the server** (`WWW_BASE_URI`) |
| File sync (ZFS) | upload/download URLs come from the API | follows the server's `S3_PUBLIC_URL` |
| Item/relation URIs | `ZOTERO_CONFIG.BASE_URI` = `http://zotero.org/` | an identifier, not fetched; the server is configured with the same value |

So **no client modification is needed**. Ways to deploy:

1. **Per user:** `client/user.js.template` → `<profile>/user.js`
   (`client/scripts/configure-profile.{sh,ps1}`).
2. **Fleet (Windows/Linux):** Mozilla AutoConfig, `client/autoconfig/` →
   `<install dir>/defaults/pref/autoconfig.js` + `<install dir>/zotero.cfg`,
   with `lockPref()`. The official 10.0.8 build ships the AutoConfig module
   (`defaults/autoconfig/prefcalls.js` in `omni.ja`); checked in the downloaded
   build. Deploy it with GPO/SCCM/Intune or a package. On macOS, changing the
   signed `.app` breaks the code signature, so use the per-profile `user.js`
   there.
3. **Patching `omni.ja` / rebuilding** (ZotPrime's way) is still possible. It
   also redirects the few remaining hard-coded links ("create group" web page,
   storage settings link), but you have to repeat it for every release and
   disable auto-update. Not recommended.

What still refers to zotero.org with prefs alone (cosmetic or optional):
links to zotero.org web pages (support, "New Group…" opens
`www.zotero.org/groups/new`, storage settings); translator/style updates
(`automaticScraperUpdates`); PDF metadata retrieval (`services.zotero.org`);
Read Aloud (cloud TTS); app updates (`app.update.auto`). All of these can be
switched off by preference in air-gapped networks. **Groups are created by an
admin** (`bin/zotadm group add`) instead of through the website.

### Login flow since Zotero 9 (the main new obstacle)

```
Zotero ──POST /keys/sessions──────────────▶ dataserver   → {sessionToken, loginURL = WWW_BASE_URI + "login?session=…"}
Zotero ──opens loginURL in browser─────────▶ login page   (zotero.org website: closed source)
login  ──POST /keys/sessions/complete──────▶ dataserver   (HTTP-Basic super-user, {sessionToken, userID, access})
dataserver ─Redis─▶ stream-server ─"loginComplete"─▶ Zotero   (fallback: client polls GET /keys/sessions/<token>)
```

Username/password login (`POST /keys`) is still in the dataserver, but the
client only uses it to migrate legacy stored passwords. `login/` implements
the website's part: a form, password check against `zotero_www.users` (bcrypt,
plus legacy SHA1/MD5 hashes as the dataserver accepts them), optional
**LDAP/AD** with automatic provisioning on first login, CSRF protection and
throttling. The dataserver creates its own user records automatically when a
session completes (`Zotero_Users::addFromWWW`), so a user only has to exist in
`zotero_www.users`.

## 3. Server side: needed changes

### Patches to upstream dataserver (`dataserver/patches/`)
1. `0001-s3-compatible-endpoint.patch` (+28 lines). Lets you configure the AWS
   SDK endpoint (`$AWS_SERVICE_CONFIG`, path-style). Upload URL and presigned
   download URLs use the client-facing `S3_PUBLIC_ENDPOINT` instead of the
   hard-coded `https://<bucket>.s3.amazonaws.com/`.
2. `0002-optional-fulltext-indexing-table.patch` (+7 lines). Upstream now
   tracks full-text index state in **DynamoDB** (with a Lambda indexer). With
   `$FULLTEXT_INDEXING_TABLE = ''`, those calls are skipped.

Both patches are kept minimal on purpose and apply cleanly to the pinned
commit (checked with `git apply --check`). ZotPrime instead replaces whole
upstream files (`ApiController.php`, `Storage.inc.php`, …) with older copies,
which silently reverts upstream fixes.

### AWS services: what's used and how it's handled
| Service | Used for | On-premise |
|---|---|---|
| S3 | attachments, full-text bodies | Garage (patch 0001) |
| DynamoDB | full-text index state; Read Aloud | skipped (patch 0002); TTS not available |
| SQS | full-text reindex queue | only reached through DynamoDB state, so never called |
| SNS | admin alerts | already a no-op when `SNS_ALERT_TOPIC` is empty |
| CloudWatch | TTS only | not used |

### Other findings
* **MySQL 8 is required.** Current code uses `INSERT … AS new ON DUPLICATE KEY
  UPDATE` (MySQL ≥ 8.0.19), which MariaDB doesn't support. ZotPrime uses
  MariaDB and has to patch around it.
* **`zotero_www` schema.** On zotero.org it belongs to the website. This repo
  ships a minimal schema covering every query the dataserver makes
  (`dataserver/init/www.sql`): `users`, `users_email.validated`, `users_meta`,
  `GDN_User`, `storage_institutions`, …
* **Storage quota.** The default is 300 MB per user. The init script adds an
  "institution" matching every validated e-mail domain with
  `DEFAULT_STORAGE_QUOTA_MB`. Per-user quotas: `bin/zotadm user quota`.
* **WebDAV is not an alternative for files** if you want groups. Zotero's
  WebDAV only covers the personal library, and metadata still needs a sync
  server. With your own dataserver, file sync uses the regular Zotero storage
  protocol for personal *and* group libraries.
* **Super-user requests:** the dataserver only accepts them from private IPs,
  and behind a reverse proxy every request appears to come from the proxy. The
  proxy therefore rejects any `Authorization: Basic` header and the internal
  endpoints (`/keys/sessions/complete`, `…/info`, `storageadmin`).
* **Full-text search through the Web API** needs an indexer that copies
  full-text content from S3 into Elasticsearch (upstream uses Lambda,
  `cloudformation/fulltext-indexing`). Desktop clients search locally and sync
  full text through S3, so this only matters for a self-hosted web library.

## 4. Choosing the S3 store: Garage vs. RustFS

Zotero needs three S3 features: browser-style **POST-policy uploads**
(`PostObject`, with `Content-MD5` and `success_action_status=201`),
**presigned GET** and **path-style** addressing. `tests/e2e.py` exercises all
three, and CI runs it against both backends.

| | Garage | RustFS |
|---|---|---|
| License / governance | AGPL-3.0, community project (Deuxfleurs), since 2020 | Apache-2.0, company-backed, contributor CLA |
| Maturity | stable 1.x/2.x line | 1.0 GA in Sep 2026 (1.0.5 in Nov 2026); before that, alpha/beta |
| Operations | own CLI: layout, keys, buckets (scripted in `setup.sh`) | MinIO-like: root key via env, buckets via any S3 client, optional web console |
| Scaling | geo-distributed replication, small footprint | erasure coding, MinIO-style multi-node pools |
| Notes | `PostObject` documented as supported | POST-policy conditions were **not enforced** up to 1.0.0-alpha.82 (CVE-2026-27607); fixed in alpha.83. Pin ≥ 1.0.x |

Both are reasonable choices. RustFS is the closer drop-in for teams that know
MinIO. Garage has the more conservative history. Its AGPL licence also matches
the dataserver, and a CLA-backed project can be relicensed later, as happened
with MinIO. For a university that already runs Ceph, using its RGW directly is
the option with the least to operate.

## 5. Browser connector

`zotero/zotero-connectors` talks to the **locally running desktop app**
(`http://127.0.0.1:23119`). That path doesn't involve any server, so the
official store versions work unchanged. Only when the desktop app is *not*
running does the connector save directly online. That path needs:

* `API_URL` hard-coded in `src/common/zotero_config.js`. Unlike the desktop
  client there's no preference for it, so the connector has to be rebuilt.
* **OAuth 1.0a** against `www.zotero.org/oauth/{request,authorize,access}`.
  That's the closed-source website again, so the login service would need to
  implement these endpoints.
* Self-distribution: Chrome/Edge via enterprise policy (self-hosted CRX +
  update manifest), Firefox only with a Mozilla signature (unlisted AMO
  signing), Safari only bundled in a signed macOS app.

So building the connector in CI is technically easy (`build.sh`), but it only
pays off together with an OAuth implementation. Recommendation: use the
official connector with the desktop app.

## 6. What this repository does NOT cover (yet)

* **Mobile apps** (iOS/Android) hard-code `api.zotero.org` and would need to be
  rebuilt.
* **Web library** (`zotero/web-library`) can be self-hosted against this API.
  Not included.
* **Browser connector without a running desktop app**: see section 5.
* **Self-service** (registration, password reset, group creation in a web UI):
  admin CLI only. LDAP removes most of the need.
* **Upgrades:** when you bump `DATASERVER_REF`, apply new
  `misc/db-updates/<date>/` scripts and re-check that the patches apply.

## 7. Risks

* **Untested end-to-end in this environment.** No Docker daemon was available
  here. The login service was unit-tested with a mocked dataserver, patches
  were checked with `git apply --check`, client packaging was run for real,
  and shell and Python syntax were checked. The CI workflow `server.yml` runs
  the full protocol test (`tests/e2e.py`) on GitHub's runners. Its first run
  is the real integration test.
* **S3 compatibility** of Garage/RustFS with Zotero's exact upload form isn't
  verified until the CI e2e test passes. Fallbacks: Ceph RGW, SeaweedFS, or
  the `pgsty/minio` community fork. Only the `S3_*` settings change.
* **Upstream drift.** Zotero doesn't support self-hosting. Breaking changes
  (like the Zotero 9 login) will happen again. Pin versions, and test new
  client releases against the server before rolling them out (AutoConfig +
  `app.update.auto=false` gives you that control).
* **Licensing:** dataserver and stream-server are AGPL-3.0. If you modify them
  and offer the service to users, you must make the modified source available.
  The patches in this repo are enough for that.
