#!/usr/bin/env python3
"""
zotadm -- user and group administration for the on-premise Zotero server.

Run via ./bin/zotadm (docker compose exec login python manage.py ...).

  user add <username> <email> [--password PW]   create local user (prompts if no --password)
  user passwd <username> [--password PW]        set/reset password
  user list                                     list users
  user disable <username> | enable <username>   block / unblock login and sync
  user quota <username> <MB|unlimited>          per-user storage quota
  group add <owner-username> <name> [--type Private|PublicClosed|PublicOpen]
  group list
  group member <groupID> <username> [--role member|admin]
  group remove-member <groupID> <username>
"""
import argparse
import getpass
import sys
import time
import xml.sax.saxutils as su

import common


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def user_id(conn, username):
    u = common.find_user(conn, username)
    if not u:
        die(f"no such user: {username}")
    return u


def ask_password(pw):
    if pw:
        return pw
    a = getpass.getpass("Password: ")
    if a != getpass.getpass("Repeat:   "):
        die("passwords do not match")
    if len(a) < 8:
        die("password must have at least 8 characters")
    return a


def cmd_user(args, conn):
    if args.action == "add":
        if common.find_user(conn, args.username):
            die("user exists")
        uid = common.create_user(conn, args.username, args.email, ask_password(args.password))
        print(f"created user {args.username} (userID {uid})")
    elif args.action == "passwd":
        u = user_id(conn, args.username)
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET password=%s, authSource='local' WHERE userID=%s",
                        (common.hash_password(ask_password(args.password)), u["userID"]))
        print("password updated")
    elif args.action == "list":
        with conn.cursor() as cur:
            cur.execute("SELECT userID, username, email, role, authSource FROM users ORDER BY userID")
            print(f"{'ID':>6}  {'username':<24} {'email':<32} {'role':<8} auth")
            for r in cur.fetchall():
                print(f"{r['userID']:>6}  {r['username']:<24} {r['email']:<32} {r['role']:<8} {r['authSource']}")
    elif args.action in ("disable", "enable"):
        u = user_id(conn, args.username)
        role = "deleted" if args.action == "disable" else "normal"
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET role=%s WHERE userID=%s", (role, u["userID"]))
        if args.action == "disable":
            # Revoke existing API keys so running clients stop syncing
            r = common.api("GET", f"users/{u['userID']}/keys")
            if r.ok:
                for k in r.json():
                    common.api("DELETE", f"keys/{k['key']}")
        print(f"user {args.username} {args.action}d")
    elif args.action == "quota":
        u = user_id(conn, args.username)
        # far-future expiration; 'unlimited' is understood by the dataserver
        r = common.api("POST", f"users/{u['userID']}/storageadmin",
                       data={"quota": args.quota, "expiration": int(time.time()) + 100 * 365 * 86400})
        if not r.ok:
            die(f"dataserver returned {r.status_code}: {r.text[:300]}")
        print(r.text.strip() or "quota set")


def cmd_group(args, conn):
    if args.action == "add":
        owner = user_id(conn, args.owner)
        xml = (f'<group owner="{owner["userID"]}" name={su.quoteattr(args.name)} type="{args.type}" '
               'libraryEditing="members" libraryReading="members" fileEditing="members" '
               'description="" url="" hasImage="0"/>')
        r = common.api("POST", "groups", data=xml.encode(), headers={"Content-Type": "text/xml"})
        if r.status_code != 201:
            die(f"dataserver returned {r.status_code}: {r.text[:300]}")
        print(f"created group: {r.headers.get('Location', r.text[:200])}")
    elif args.action == "list":
        r = common.api("GET", "groups?format=json&limit=100")  # super-user: all groups
        if not r.ok:
            die(f"dataserver returned {r.status_code}: {r.text[:300]}")
        for g in r.json():
            d = g.get("data", {})
            print(f"{g['id']:>6}  {d.get('name', ''):<32} {d.get('type', ''):<13} owner={d.get('owner')}")
    elif args.action == "member":
        u = user_id(conn, args.username)
        r = common.api("POST", f"groups/{args.group}/users",
                       data=f'<user id="{u["userID"]}" role="{args.role}"/>'.encode(),
                       headers={"Content-Type": "text/xml"})
        if not r.ok:
            die(f"dataserver returned {r.status_code}: {r.text[:300]}")
        print(f"added {args.username} to group {args.group} as {args.role}")
    elif args.action == "remove-member":
        u = user_id(conn, args.username)
        r = common.api("DELETE", f"groups/{args.group}/users/{u['userID']}")
        if not r.ok:
            die(f"dataserver returned {r.status_code}: {r.text[:300]}")
        print("removed")


def main():
    p = argparse.ArgumentParser(prog="zotadm", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    u = sub.add_parser("user").add_subparsers(dest="action", required=True)
    a = u.add_parser("add"); a.add_argument("username"); a.add_argument("email"); a.add_argument("--password")
    a = u.add_parser("passwd"); a.add_argument("username"); a.add_argument("--password")
    u.add_parser("list")
    u.add_parser("disable").add_argument("username")
    u.add_parser("enable").add_argument("username")
    a = u.add_parser("quota"); a.add_argument("username"); a.add_argument("quota")

    g = sub.add_parser("group").add_subparsers(dest="action", required=True)
    a = g.add_parser("add"); a.add_argument("owner"); a.add_argument("name")
    a.add_argument("--type", default="Private", choices=["Private", "PublicClosed", "PublicOpen"])
    g.add_parser("list")
    a = g.add_parser("member"); a.add_argument("group", type=int); a.add_argument("username")
    a.add_argument("--role", default="member", choices=["member", "admin"])
    a = g.add_parser("remove-member"); a.add_argument("group", type=int); a.add_argument("username")

    args = p.parse_args()
    conn = common.db()
    try:
        {"user": cmd_user, "group": cmd_group}[args.cmd](args, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
