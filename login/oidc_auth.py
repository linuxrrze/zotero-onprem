"""Optional OpenID Connect login (LOGIN_AUTH_BACKENDS=oidc, e.g. oidc,local).

The login page sends the user to the institution's identity provider
(Keycloak, Entra ID, Shibboleth IdP with the OIDC plugin, ...); on return the
user confirms that the Zotero client may be linked to the account.

  OIDC_ISSUER          https://idp.example.org/realms/uni   (discovery:
                       <issuer>/.well-known/openid-configuration)
  OIDC_CLIENT_ID       client registered at the IdP; redirect URI:
                       https://<ZOTERO_HOST>/login/oidc/callback
  OIDC_CLIENT_SECRET
  OIDC_SCOPES          openid email profile          (add e.g. "eduperson_affiliation")
  OIDC_USERNAME_CLAIM  preferred_username            (fallback: e-mail local part, sub)
  OIDC_EMAIL_CLAIM     email
  OIDC_FETCH_USERINFO  1 = also read the userinfo endpoint (claims not in the ID token)
  OIDC_ALLOWED_CLAIM   e.g. eduperson_affiliation    (empty = every account may log in)
  OIDC_ALLOWED_VALUES  e.g. student,staff,employee,faculty
                       Scoped values ("staff@uni.example") match by their part
                       before "@" too.
  OIDC_BUTTON_LABEL    Log in with your university account

Accounts are matched by the stable (issuer, sub) pair -- never by username or
e-mail -- and created on first login. The e-mail address is refreshed on every
login (a validated address grants the institutional storage quota).
"""
import logging
import re

import common
from common import env

log = logging.getLogger("zotero-login.oidc")

AUTH_SOURCE = "oidc"


def _setting(name, default=""):
    return env(name, "") or default


ISSUER = _setting("OIDC_ISSUER").rstrip("/")
CLIENT_ID = _setting("OIDC_CLIENT_ID")
CLIENT_SECRET = _setting("OIDC_CLIENT_SECRET")
SCOPES = _setting("OIDC_SCOPES", "openid email profile")
USERNAME_CLAIM = _setting("OIDC_USERNAME_CLAIM", "preferred_username")
EMAIL_CLAIM = _setting("OIDC_EMAIL_CLAIM", "email")
FETCH_USERINFO = _setting("OIDC_FETCH_USERINFO", "1") == "1"
ALLOWED_CLAIM = _setting("OIDC_ALLOWED_CLAIM")
ALLOWED_VALUES = {v.strip().lower() for v in _setting("OIDC_ALLOWED_VALUES").split(",") if v.strip()}
BUTTON_LABEL = _setting("OIDC_BUTTON_LABEL", "Log in with your university account")

oauth = None  # authlib registry, set by init_app()


def init_app(app):
    """Register the IdP with Authlib. Discovery happens lazily on first use."""
    global oauth
    missing = [n for n, v in (("OIDC_ISSUER", ISSUER), ("OIDC_CLIENT_ID", CLIENT_ID),
                              ("OIDC_CLIENT_SECRET", CLIENT_SECRET)) if not v]
    if missing:
        raise RuntimeError("LOGIN_AUTH_BACKENDS includes oidc, but %s not set" % ", ".join(missing))
    if "openid" not in SCOPES.split():
        raise RuntimeError("OIDC_SCOPES must include 'openid'")

    from authlib.integrations.flask_client import OAuth

    oauth = OAuth(app)
    oauth.register(
        "idp",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        server_metadata_url=ISSUER + "/.well-known/openid-configuration",
        client_kwargs={"scope": SCOPES, "code_challenge_method": "S256"},
    )


def authorize_redirect(redirect_uri):
    return oauth.idp.authorize_redirect(redirect_uri)


def fetch_claims():
    """Finish the authorization-code flow; return the verified claims.

    Authlib validates the ID token (signature, issuer, audience, expiry, nonce).
    """
    token = oauth.idp.authorize_access_token()
    claims = dict(token.get("userinfo") or {})
    if not claims.get("sub"):
        raise ValueError("ID token without 'sub'")
    if FETCH_USERINFO and oauth.idp.load_server_metadata().get("userinfo_endpoint"):
        extra = dict(oauth.idp.userinfo(token=token))
        # OIDC Core 5.3.2: the userinfo 'sub' must match the ID token
        if extra.get("sub") != claims["sub"]:
            raise ValueError("userinfo 'sub' does not match the ID token")
        claims = {**extra, **claims}
    return claims


# ------------------------------------------------------------------- claims

def claim_values(claims, name):
    """A claim as a list of strings (claims can be a string, list or absent)."""
    value = claims.get(name)
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def is_allowed(claims):
    """Restrict who may log in, e.g. by eduPerson affiliation."""
    if not ALLOWED_CLAIM:
        return True
    for value in claim_values(claims, ALLOWED_CLAIM):
        v = value.strip().lower()
        if v in ALLOWED_VALUES or v.split("@", 1)[0] in ALLOWED_VALUES:
            return True
    return False


def external_id(claims):
    """Stable identity: subject identifiers are only unique per issuer."""
    return f"{claims.get('iss') or ISSUER}|{claims['sub']}"


def email_of(claims):
    values = claim_values(claims, EMAIL_CLAIM)
    if not values or claims.get("email_verified") is False:
        return ""
    return values[0].strip()


def username_of(claims):
    """Zotero username suggestion: letters, digits, . _ - only."""
    for candidate in (claim_values(claims, USERNAME_CLAIM)[:1]
                      + [email_of(claims).split("@", 1)[0]]
                      + [str(claims["sub"])]):
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", (candidate or "").strip()).strip(".-_").lower()
        if name:
            return name[:60]
    return "user"


# ------------------------------------------------------------- provisioning

def user_for(claims):
    """Find or create the zotero_www user for verified claims."""
    ext = external_id(claims)
    email = email_of(claims)
    conn = common.db()
    try:
        user = common.find_external(conn, AUTH_SOURCE, ext)
        if user is None:
            username = common.unique_username(conn, username_of(claims))
            user_id = common.create_user(conn, username, email, auth_source=AUTH_SOURCE)
            common.link_external(conn, AUTH_SOURCE, ext, user_id)
            log.info("provisioned OIDC user %s (userID %s) for %s", username, user_id, ext)
            user = common.get_user(conn, user_id)
        elif email and email != user["email"]:
            common.update_email(conn, user["userID"], email)
            user["email"] = email
        return user
    finally:
        conn.close()
