"""Shared helpers: configuration, database access, password hashing, dataserver API."""
import hashlib
import os
import secrets

import bcrypt
import pymysql
import requests


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"Environment variable {name} must be set")
    return value


# Internal (container-network) URL of the dataserver, e.g. http://dataserver/
API_INTERNAL_URL = env("ZOTERO_API_INTERNAL_URL", "http://dataserver/").rstrip("/") + "/"
SUPER_USER = env("ZOTERO_SUPER_USER", required=True)
SUPER_PASSWORD = env("ZOTERO_SUPER_PASSWORD", required=True)
AUTH_SALT = env("ZOTERO_AUTH_SALT", "")

# Permissions granted to the API key the desktop client receives
CLIENT_KEY_ACCESS = {
    "user": {"library": True, "notes": True, "write": True, "files": True},
    "groups": {"all": {"library": True, "write": True}},
}


# --------------------------------------------------------------------------- DB

def db():
    return pymysql.connect(
        host=env("MYSQL_HOST", "mysql"),
        port=int(env("MYSQL_PORT", "3306")),
        user=env("MYSQL_USER", required=True),
        password=env("MYSQL_PASSWORD", required=True),
        database="zotero_www",
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def find_user(conn, login):
    """Look up a user by username or (validated) e-mail address."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT userID, username, password, email, role, authSource FROM users "
            "WHERE username=%s", (login,))
        row = cur.fetchone()
        if row or "@" not in login:
            return row
        cur.execute(
            "SELECT u.userID, u.username, u.password, u.email, u.role, u.authSource "
            "FROM users u LEFT JOIN users_email e USING (userID) "
            "WHERE u.email=%s OR (e.email=%s AND e.validated=1) LIMIT 1", (login, login))
        return cur.fetchone()


def create_user(conn, username, email, password=None, auth_source="local"):
    pw_hash = hash_password(password) if password else ""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, password, email, authSource) VALUES (%s,%s,%s,%s)",
            (username, pw_hash, email, auth_source))
        user_id = cur.lastrowid
        if email:
            # validated=1 so institutional (default) storage quota applies
            cur.execute(
                "INSERT INTO users_email (userID, email, validated) VALUES (%s,%s,1)",
                (user_id, email))
    return user_id


def set_banned(conn, user_id, banned):
    """GDN_User.Banned is what the dataserver checks for every API request
    (Zotero_Users::isValidUser; cached in memcached for up to 5 minutes)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO GDN_User (UserID, Banned) VALUES (%s,%s) "
            "ON DUPLICATE KEY UPDATE Banned=VALUES(Banned)", (user_id, 1 if banned else 0))


def get_user(conn, user_id):
    with conn.cursor() as cur:
        cur.execute("SELECT userID, username, password, email, role, authSource FROM users "
                    "WHERE userID=%s", (user_id,))
        return cur.fetchone()


def find_external(conn, source, external_id):
    """User linked to a stable external identity (e.g. OIDC issuer+sub), or None."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT u.userID, u.username, u.password, u.email, u.role, u.authSource "
            "FROM users_external x JOIN users u USING (userID) "
            "WHERE x.authSource=%s AND x.externalID=%s", (source, external_id))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE users_external SET lastLogin=NOW() "
                        "WHERE authSource=%s AND externalID=%s", (source, external_id))
        return row


def link_external(conn, source, external_id, user_id):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO users_external (authSource, externalID, userID, lastLogin) "
                    "VALUES (%s,%s,%s,NOW())", (source, external_id, user_id))


def unique_username(conn, base):
    """`base`, or `base-2`, `base-3`, ... -- whichever is still free."""
    base = base[:200] or "user"
    with conn.cursor() as cur:
        for n in range(1, 1000):
            name = base if n == 1 else f"{base}-{n}"
            cur.execute("SELECT 1 FROM users WHERE username=%s", (name,))
            if not cur.fetchone():
                return name
    raise RuntimeError(f"no free username for {base!r}")


def update_email(conn, user_id, email):
    """Keep the account's e-mail in sync with the identity provider. A
    validated address is what grants the institutional storage quota."""
    if not email:
        return
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET email=%s WHERE userID=%s", (email, user_id))
        cur.execute("SELECT 1 FROM users_email WHERE userID=%s AND email=%s", (user_id, email))
        if not cur.fetchone():
            cur.execute("INSERT INTO users_email (userID, email, validated) VALUES (%s,%s,1)",
                        (user_id, email))


# --------------------------------------------------------------------- passwords

def hash_password(password):
    # PHP password_verify() and Python bcrypt both accept the $2y$ prefix
    h = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
    return "$2y$" + h[4:]


def verify_password(password, stored):
    """Same algorithms the dataserver accepts (model/auth/Password.inc.php)."""
    if not stored:
        return False
    if stored.startswith("$2"):
        try:
            return bcrypt.checkpw(password.encode(), ("$2b$" + stored[4:]).encode())
        except ValueError:
            return False
    if len(stored) == 40:  # legacy salted SHA1
        return secrets.compare_digest(
            hashlib.sha1((AUTH_SALT + password).encode()).hexdigest(), stored)
    if len(stored) == 32:  # legacy MD5
        return secrets.compare_digest(hashlib.md5(password.encode()).hexdigest(), stored)
    return False


# --------------------------------------------------------------- dataserver API

def api(method, path, **kwargs):
    """Request to the dataserver as API super-user (HTTP Basic auth)."""
    headers = kwargs.pop("headers", {})
    headers.setdefault("Zotero-API-Version", "3")
    return requests.request(
        method, API_INTERNAL_URL + path.lstrip("/"),
        auth=(SUPER_USER, SUPER_PASSWORD), headers=headers, timeout=15, **kwargs)
