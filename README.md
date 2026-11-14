# Zotero on-premise

A Docker Compose stack that runs the complete Zotero sync infrastructure in
your own network: metadata sync, file storage, group libraries, instant-sync
push and account login. It works with the **official, unmodified Zotero desktop
client**, including the browser login that Zotero 9 introduced (checked against
the current client source and the 10.0.8 release build).

Background, design decisions and limitations: **[docs/FINDINGS.md](docs/FINDINGS.md)**.

```
                       ┌──────────── docker compose ─────────────────────────────────┐
 Zotero desktop ─HTTPS─┤ caddy ─┬─ /            → dataserver (PHP, upstream + 3 patches) │
  (stock client,       │ (TLS)  ├─ /login       → login  (replaces zotero.org login page)│
   2 prefs set)        │        ├─ /stream/     → stream-server (WebSocket push)         │
              ─HTTPS───┤        └─ S3 host      → garage | rustfs (S3 object storage)    │
 browser (login) ──────┤                           mysql 8 · redis · memcached ·         │
                       │                           tinymce-clean · [elasticsearch]       │
                       └─────────────────────────────────────────────────────────────────┘
```

## Requirements

* Linux host with Docker Engine + Compose plugin, 4 GB RAM, disk for attachments
* Two DNS names pointing to the host, e.g. `zotero.uni.example` and
  `s3.zotero.uni.example` (the file store needs its own host name)
* A TLS certificate for both, or Caddy's internal CA (then distribute its root
  certificate to clients)
* Outbound access to GitHub, Docker Hub and package mirrors **at build time
  only**. To run it air-gapped, build the images once and transfer them with
  `docker save` / `docker load`.

## Installation

```bash
cp .env.example .env
$EDITOR .env                 # ZOTERO_HOST, S3_HOST, CADDY_TLS, S3 backend, (LDAP_*)
./bin/setup.sh               # generates secrets, builds images, sets up storage, starts everything
./bin/zotadm user add alice alice@uni.example
```

**S3 backend:** Garage (default) or RustFS:

| | `COMPOSE_PROFILES` | `S3_BACKEND_PORT` |
|---|---|---|
| Garage | `garage` | `3900` |
| RustFS | `rustfs` | `9000` |

Any other S3 store (e.g. an existing Ceph RGW) also works. Point the
dataserver's `S3_INTERNAL_URL` and `S3_PUBLIC_URL` (in `docker-compose.yml`) at
it, set `COMPOSE_PROFILES=` to empty, and create the buckets `zotero` and
`zotero-fulltext`. The store must support POST-policy uploads, presigned GET
and path-style addressing.

**Prebuilt images:** set `IMAGE_PREFIX=ghcr.io/<owner>/zotero-onprem` in
`.env` and run `docker compose pull` instead of building locally (see CI below).

For an institutional certificate, put `fullchain.pem` and `privkey.pem` into
`./certs/` and set `CADDY_TLS="/certs/fullchain.pem /certs/privkey.pem"`.

Check it's running:

```bash
docker compose ps
curl -s https://$ZOTERO_HOST/itemTypes | head -c 200     # API answers
```

## Connecting Zotero desktop

Two preferences point an unmodified client at the server:

| Preference | Value |
|---|---|
| `extensions.zotero.api.url` | `https://ZOTERO_HOST/` |
| `extensions.zotero.streaming.url` | `wss://ZOTERO_HOST/stream/` |

* **Single machine:** Zotero → Settings → Advanced → Config Editor, then set
  both values and restart. Or run `client/scripts/configure-profile.sh ZOTERO_HOST`
  (`.ps1` on Windows).
* **Managed machines (locked):** `client/scripts/install-autoconfig.sh /opt/zotero ZOTERO_HOST`.
  On Windows, copy `client/autoconfig/defaults/pref/autoconfig.js` and
  `zotero.cfg` (with the host filled in) into `C:\Program Files\Zotero\`.

Then use **Settings → Sync → Log In**. A browser opens your login page; after
logging in, Zotero is linked and syncs. Details: [client/README.md](client/README.md).

## Administration

```bash
./bin/zotadm user add <username> <email> [--password PW]
./bin/zotadm user passwd <username>
./bin/zotadm user list
./bin/zotadm user disable <username>        # blocks login, revokes API keys
./bin/zotadm user quota <username> 50000    # MB, or "unlimited"
./bin/zotadm group add <owner> "Research Group" [--type Private]
./bin/zotadm group member <groupID> <username> [--role admin]
./bin/zotadm group list
```

With `LOGIN_AUTH_BACKENDS=ldap` (or `local,ldap`), directory users are created
automatically on their first login, so no `user add` is needed.

## Backup

Back up the two volumes that hold state, at the same moment if you can:

* MySQL: `docker compose exec mysql mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --all-databases > zotero.sql`
* Garage: volumes `zotero_garage-meta` and `zotero_garage-data` (stop Garage,
  or use its metadata snapshots plus a file-level copy of `garage-data`)
* `.env`, which holds the secrets

## Upgrading

* **Clients:** auto-update keeps working because the settings are prefs, not a
  patched binary. For tighter control, set `app.update.auto=false` and test new
  Zotero releases against the server first.
* **Server:** change `DATASERVER_REF` in `dataserver/Dockerfile`, then run
  `docker compose build --pull`. Apply any new SQL in the dataserver's
  `misc/db-updates/<date>/` to `zotero_master` and the shards, then
  `docker compose up -d` (the `init` service re-runs `schema_update`). If a
  patch no longer applies, the build fails loudly.

## CI (GitHub Actions)

| Workflow | What it does |
|---|---|
| `.github/workflows/server.yml` | Starts the whole stack on a runner, once per S3 backend, and runs `tests/e2e.py`. The test covers browser login, API key, item creation, file upload to S3 and download, deduplication, the stream WebSocket, and that the proxy blocks internal endpoints. If it passes, builds `linux/amd64` + `linux/arm64` images and pushes them to GHCR. Runs on push, on PRs (test only) and weekly. |
| `.github/workflows/client.yml` | Downloads the **official** Zotero release and adds the AutoConfig files for your server. Outputs: Linux x86_64/arm64 tarballs, Windows portable zip, Windows silent-install kit (`setup.exe` + `install.ps1` for GPO/Intune/SCCM), macOS kit. Checks daily and publishes a `client-<version>` release whenever Zotero ships a new version. Needs the repository variable `ZOTERO_HOST`. |

The client packages can also be built locally:
`client/scripts/package.sh zotero.uni.example latest dist/`.

## Layout

```
docker-compose.yml        stack definition
.env.example              configuration template
bin/setup.sh, bin/zotadm  setup and administration
dataserver/               Dockerfile, config (env-driven), patches, DB init + zotero_www schema
login/                    login page for Zotero ≥ 9 (Flask), zotadm CLI, optional LDAP
stream-server/            Dockerfile (upstream)
tinymce-clean-server/     Dockerfile (upstream)
proxy/Caddyfile           TLS + routing, blocks super-user access from outside
garage/garage.toml        S3 object store (Garage; RustFS needs no config file)
tests/e2e.py              protocol-level end-to-end test
.github/workflows/        CI: server e2e + images, client packages
mysql/zotero.cnf          MySQL 8 settings
client/                   user.js template, AutoConfig files, deployment scripts
docs/FINDINGS.md          investigation report
```

## Status

This is a **proposal and starting point, not yet run end-to-end**: no Docker
daemon was available where it was written. The first CI run of
`server.yml` is the real integration test. The patches apply to the pinned
upstream commit, and the login service passes unit tests against a mocked
dataserver. Expect some fixes in a first test deployment. The most likely
spots are listed under "Risks" in `docs/FINDINGS.md`.
