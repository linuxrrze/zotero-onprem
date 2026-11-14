"""Optional LDAP / Active Directory authentication (LOGIN_AUTH_BACKENDS=ldap or local,ldap).

Search-then-bind: find the user's DN with a service account, then bind as the
user with the supplied password.

  LDAP_URL            ldaps://ldap.example.org
  LDAP_BIND_DN        cn=zotero,ou=services,dc=example,dc=org   (empty = anonymous search)
  LDAP_BIND_PASSWORD  ...
  LDAP_SEARCH_BASE    ou=people,dc=example,dc=org
  LDAP_USER_FILTER    (&(objectClass=person)(|(uid={login})(mail={login})))
  LDAP_USERNAME_ATTR  uid            (sAMAccountName for AD)
  LDAP_EMAIL_ATTR     mail
"""
import logging

from common import env

log = logging.getLogger("zotero-login.ldap")


def authenticate(login, password):
    """Return {'username', 'email'} if the directory accepts the credentials."""
    url = env("LDAP_URL")
    if not url or not password:
        return None

    from ldap3 import ALL, Connection, Server
    from ldap3.utils.conv import escape_filter_chars

    server = Server(url, get_info=ALL, connect_timeout=5)
    user_attr = env("LDAP_USERNAME_ATTR", "uid")
    mail_attr = env("LDAP_EMAIL_ATTR", "mail")
    flt = env("LDAP_USER_FILTER", "(|(uid={login})(mail={login}))").replace(
        "{login}", escape_filter_chars(login))

    try:
        bind_dn = env("LDAP_BIND_DN")
        svc = Connection(server, user=bind_dn or None, password=env("LDAP_BIND_PASSWORD") or None,
                         auto_bind=True, raise_exceptions=True)
        svc.search(env("LDAP_SEARCH_BASE", required=True), flt,
                   attributes=[user_attr, mail_attr], size_limit=2)
        if len(svc.entries) != 1:
            return None
        entry = svc.entries[0]
        user_dn = entry.entry_dn
        svc.unbind()

        conn = Connection(server, user=user_dn, password=password, raise_exceptions=False)
        if not conn.bind():
            return None
        conn.unbind()
    except Exception as e:  # directory unreachable, bad config, ...
        log.error("LDAP error: %s", e)
        return None

    username = str(entry[user_attr].value) if user_attr in entry else login
    email = str(entry[mail_attr].value) if mail_attr in entry and entry[mail_attr].value else ""
    return {"username": username, "email": email}
