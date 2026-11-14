#!/usr/bin/env bash
#
# First-time setup / idempotent re-run:
#   1. create .env from .env.example and generate missing secrets
#   2. build images
#   3. start the S3 backend (Garage or RustFS) and create key/buckets
#   4. start the whole stack (the "init" service creates the databases)
#
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || cp .env.example .env
chmod 600 .env   # holds every secret

hex() { openssl rand -hex "$1"; }
set_secret() {   # set_secret NAME VALUE -- only if empty in .env
	local name=$1 value=$2
	if grep -qE "^${name}=\s*$" .env; then
		sed -i.bak "s|^${name}=.*|${name}=${value}|" .env && rm -f .env.bak
		echo "generated $name"
	fi
}
set_secret MYSQL_ROOT_PASSWORD "$(hex 24)"
set_secret MYSQL_PASSWORD      "$(hex 24)"
set_secret ZOTERO_AUTH_SALT    "$(hex 16)"
set_secret ZOTERO_SUPER_PASSWORD "$(hex 24)"
set_secret LOGIN_SECRET_KEY    "$(hex 32)"
set_secret S3_ACCESS_KEY       "GK$(hex 12)"
set_secret S3_SECRET_KEY       "$(hex 32)"
set_secret GARAGE_RPC_SECRET   "$(hex 32)"
set_secret GARAGE_ADMIN_TOKEN  "$(hex 32)"

# read a value from .env without sourcing it (values may contain shell syntax)
envval() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/^["'"'"']//; s/["'"'"']$//'; }
ZOTERO_HOST=$(envval ZOTERO_HOST); S3_ACCESS_KEY=$(envval S3_ACCESS_KEY); S3_SECRET_KEY=$(envval S3_SECRET_KEY)
if [[ "$ZOTERO_HOST" == *example.org ]]; then
	echo "Please set ZOTERO_HOST and S3_HOST in .env first." >&2; exit 1
fi

if [ -n "$(envval IMAGE_PREFIX)" ]; then
	echo "== pulling prebuilt images from $(envval IMAGE_PREFIX)"
	docker compose pull
else
	docker compose build
fi

backend=$(envval COMPOSE_PROFILES); backend=${backend:-garage}
export COMPOSE_PROFILES=$backend

if [[ ",$backend," == *",garage,"* ]]; then
	echo "== configuring Garage"
	docker compose up -d garage
	garage() { docker compose exec -T garage /garage "$@"; }
	for i in $(seq 1 30); do garage status >/dev/null 2>&1 && break; sleep 2; done

	if ! garage layout show 2>/dev/null | grep -q "dc1"; then
		node=$(garage node id -q | cut -d@ -f1)
		garage layout assign -z dc1 -c "${GARAGE_CAPACITY:-1T}" "$node"
		garage layout apply --version 1
	fi
	if ! garage key info zotero >/dev/null 2>&1; then
		garage key import --yes -n zotero "$S3_ACCESS_KEY" "$S3_SECRET_KEY"
	fi
	for b in zotero zotero-fulltext; do
		garage bucket info "$b" >/dev/null 2>&1 || garage bucket create "$b"
		garage bucket allow --read --write --owner "$b" --key zotero
	done
elif [[ ",$backend," == *",rustfs,"* ]]; then
	echo "== configuring RustFS"
	docker compose up -d --wait rustfs
	# RustFS's root credentials are the S3 key; create buckets via the S3 API
	net="$(docker compose ps -q rustfs | xargs docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}')"
	aws() { docker run --rm --network "$net" -e AWS_ACCESS_KEY_ID="$S3_ACCESS_KEY" \
		-e AWS_SECRET_ACCESS_KEY="$S3_SECRET_KEY" -e AWS_DEFAULT_REGION=us-east-1 \
		amazon/aws-cli:2.22.0 --endpoint-url http://s3:9000 "$@"; }
	for b in zotero zotero-fulltext; do
		aws s3api head-bucket --bucket "$b" >/dev/null 2>&1 || aws s3api create-bucket --bucket "$b"
	done
else
	echo "== external/other S3 backend '$backend': create buckets 'zotero' and 'zotero-fulltext' yourself"
fi

echo "== starting stack"
if ! docker compose up -d; then
	echo "!! stack failed to start -- output of the init service:" >&2
	docker compose logs --no-color init >&2 || true
	exit 1
fi
docker compose logs init | tail -n 5

cat <<MSG

Done. Next steps:
  ./bin/zotadm user add <username> <email>     # create a first user
  client/README.md                             # point Zotero desktop clients here:
      extensions.zotero.api.url       = https://${ZOTERO_HOST}/
      extensions.zotero.streaming.url = wss://${ZOTERO_HOST}/stream/
MSG
