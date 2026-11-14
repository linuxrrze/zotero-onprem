#!/bin/bash
#
# One-shot, idempotent initialisation of the Zotero databases.
# Runs inside the dataserver image (service "init" in docker-compose.yml).
#
set -euo pipefail

: "${MYSQL_HOST:=mysql}"
: "${MYSQL_ROOT_PASSWORD:?}"
: "${MYSQL_USER:?}"
: "${MYSQL_PASSWORD:?}"
: "${DEFAULT_STORAGE_QUOTA_MB:=10000}"
: "${DEFAULT_STORAGE_DOMAIN_REGEX:=.+}"

MISC=/var/www/zotero/misc
mysql_root() { mysql -h "$MYSQL_HOST" -u root -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 "$@"; }

echo "[init] waiting for MySQL at $MYSQL_HOST ..."
until mysql_root -e 'SELECT 1' >/dev/null 2>&1; do sleep 2; done

if mysql_root -N -e "SHOW DATABASES LIKE 'zotero_master'" | grep -q zotero_master; then
	echo "[init] databases already exist -- running schema update only"
else
	echo "[init] creating databases"
	for db in zotero_master zotero_shard_1 zotero_shard_2 zotero_ids zotero_www; do
		mysql_root -e "CREATE DATABASE \`$db\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
	done

	echo "[init] loading master schema"
	mysql_root zotero_master < "$MISC/master.sql"
	mysql_root zotero_master < "$MISC/coredata.sql"

	# Two shards on the same MySQL host (more can be added later)
	mysql_root zotero_master -e "
		INSERT INTO shardHosts (shardHostID, address, port, state, acceptNewLibraries)
			VALUES (1, '$MYSQL_HOST', 3306, 'up', 1);
		INSERT INTO shards (shardID, shardHostID, db, state, items) VALUES
			(1, 1, 'zotero_shard_1', 'up', 0),
			(2, 1, 'zotero_shard_2', 'up', 0);"

	echo "[init] loading shard schema"
	for s in zotero_shard_1 zotero_shard_2; do
		mysql_root "$s" < "$MISC/shard.sql"
		mysql_root "$s" < "$MISC/triggers.sql"
	done

	echo "[init] loading id + www schema"
	mysql_root zotero_ids < "$MISC/ids.sql"
	mysql_root zotero_www < /opt/zotero-init/www.sql

	# Site-wide default storage quota via an "institution" matching all
	# validated e-mail domains (adjust in zotero_www.storage_institutions)
	mysql_root zotero_www -e "
		INSERT INTO storage_institutions (domain, domainBlacklist, storageQuota)
			VALUES ('$DEFAULT_STORAGE_DOMAIN_REGEX', '', $DEFAULT_STORAGE_QUOTA_MB);"
fi

# www.sql only has CREATE TABLE IF NOT EXISTS: re-applying it on every start
# adds tables introduced by later versions of this repository.
mysql_root zotero_www < /opt/zotero-init/www.sql

echo "[init] granting privileges to $MYSQL_USER"
mysql_root -e "CREATE USER IF NOT EXISTS '$MYSQL_USER'@'%' IDENTIFIED BY '$MYSQL_PASSWORD'"
for db in zotero_master zotero_shard_1 zotero_shard_2 zotero_ids zotero_www; do
	mysql_root -e "GRANT ALL PRIVILEGES ON \`$db\`.* TO '$MYSQL_USER'@'%'"
done
mysql_root -e "FLUSH PRIVILEGES"

echo "[init] updating item-type/field schema from zotero-schema"
cd /var/www/zotero/admin && php schema_update

echo "[init] done"
