"""Optional LDAP / Active Directory authentication (LOGIN_AUTH_BACKENDS=ldap or local,ldap).

Search-then-bind: find the user's DN with a service account, then bind as the
user with the supplied password.

  LDAP_URL            ldaps://ldap.example.org   (or ldap://... with LDAP_STARTTLS=1)
  LDAP_STARTTLS       1 = upgrade an ldap:// connection with StartTLS
  LDAP_CA_CERT        PEM file with the CA that signed the directory's certificate
                      (default: the system CA store); e.g. /certs/ldap-ca.pem
  LDAP_ALLOW_PLAINTEXT 1 = allow ldap:// without TLS (passwords in clear text --
                      only for a directory on the same host/test setups)
  LDAP_BIND_DN        cn=zotero,ou=services,dc=example,dc=org   (empty = anonymous search)
  LDAP_BIND_PASSWORD  ...
  LDAP_SEARCH_BASE    ou=people,dc=example,dc=org
  LDAP_USER_FILTER    (&(objectClass=person)(|(uid={login})(mail={login})))
  LDAP_USERNAME_ATTR  uid            (sAMAccountName for AD)
  LDAP_EMAIL_ATTR     mail

The server certificate is always verified (chain and host name) for ldaps://
and StartTLS.
"""
import logging
import ssl

from common import env

log = logging.getLogger("zotero-login.ldap")

DEFAULT_FILTER = "(|(uid={login})(mail={login}))"


def _setting(name, default=""):
    # docker compose passes unset variables as empty strings
    return env(name, "") or default


def _server():
    from ldap3 import NONE, Server, Tls

    url = _setting("LDAP_URL")
    starttls = _setting("LDAP_STARTTLS") == "1"
    if url.lower().startswith("ldap://") and not starttls and _setting("LDAP_ALLOW_PLAINTEXT") != "1":
        raise RuntimeError("LDAP_URL is ldap:// without LDAP_STARTTLS=1: refusing to send "
                           "passwords in clear text (set LDAP_ALLOW_PLAINTEXT=1 to override)")
    # ldap3 defaults to CERT_NONE; require a valid chain + matching host name
    tls = Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=_setting("LDAP_CA_CERT") or None)
    return Server(url, get_info=NONE, connect_timeout=5, tls=tls), starttls


def _connect(server, starttls, user, password):
    from ldap3 import Connection

    conn = Connection(server, user=user, password=password, raise_exceptions=True)
    conn.open()
    if starttls:
        conn.start_tls()
    return conn


def _first(entry, attr):
    """First value of an attribute ('' if missing); multi-valued uid/mail happen."""
    if attr not in entry:
        return ""
    values = entry[attr].values
    return str(values[0]) if values else ""


def authenticate(login, password):
    """Return {'username', 'email'} if the directory accepts the credentials."""
    if not _setting("LDAP_URL") or not password:
        return None

    from ldap3.core.exceptions import LDAPBindError, LDAPInvalidCredentialsResult
    from ldap3.utils.conv import escape_filter_chars

    user_attr = _setting("LDAP_USERNAME_ATTR", "uid")
    mail_attr = _setting("LDAP_EMAIL_ATTR", "mail")
    flt = _setting("LDAP_USER_FILTER", DEFAULT_FILTER).replace("{login}", escape_filter_chars(login))

    try:
        base = _setting("LDAP_SEARCH_BASE")
        if not base:
            raise RuntimeError("LDAP_SEARCH_BASE must be set")
        server, starttls = _server()
        svc = _connect(server, starttls, _setting("LDAP_BIND_DN") or None,
                       _setting("LDAP_BIND_PASSWORD") or None)
        svc.bind()
        svc.search(base, flt, attributes=[user_attr, mail_attr], size_limit=2)
        if len(svc.entries) != 1:
            svc.unbind()
            return None
        entry = svc.entries[0]
        user_dn = entry.entry_dn
        svc.unbind()

        conn = _connect(server, starttls, user_dn, password)
        try:
            conn.bind()
        except (LDAPBindError, LDAPInvalidCredentialsResult):
            return None
        finally:
            conn.unbind()
    except Exception as e:  # directory unreachable, TLS/certificate error, bad config, ...
        log.error("LDAP error: %s", e)
        return None

    return {"username": _first(entry, user_attr) or login, "email": _first(entry, mail_attr)}
