# Client configuration

The official Zotero desktop client is used **unmodified**. Two preferences
redirect sync to the on-premise server:

```js
extensions.zotero.api.url       = "https://ZOTERO_HOST/"
extensions.zotero.streaming.url = "wss://ZOTERO_HOST/stream/"
```

## Options

| Method | Scope | Files |
|---|---|---|
| Config Editor (Settings → Advanced) | one profile, by hand | – |
| `user.js` in the profile | one user, all their profiles | `user.js.template`, `scripts/configure-profile.sh` / `.ps1` |
| Mozilla AutoConfig (`lockPref`) | whole installation, users can't change it | `autoconfig/defaults/pref/autoconfig.js`, `autoconfig/zotero.cfg`, `scripts/install-autoconfig.sh` |

Profile locations: Linux `~/.zotero/zotero/<profile>/`, macOS
`~/Library/Application Support/Zotero/Profiles/<profile>/`, Windows
`%APPDATA%\Zotero\Zotero\Profiles\<profile>\`.

AutoConfig files go into the installation directory. On Windows that's
`C:\Program Files\Zotero\zotero.cfg` and
`C:\Program Files\Zotero\defaults\pref\autoconfig.js`; on Linux, the unpacked
tarball directory. **macOS:** don't modify `Zotero.app`, because that breaks
the code signature. Use `user.js` instead.

## Logging in

Settings → Sync → **Log In** opens `https://ZOTERO_HOST/login?session=…` in
the default browser. After a successful login the client receives an API key
and starts syncing. The page can be closed.

## Certificates

Zotero uses its own (Firefox/NSS) certificate store. With an internal CA:

* Windows/macOS: `security.enterprise_roots.enabled = true` (already in the
  templates) makes Zotero trust the CA from the OS store.
* Linux: import the CA into the profile, e.g.
  `certutil -A -n "Uni CA" -t "C,," -i ca.crt -d sql:<profile dir>`
  (package `libnss3-tools`). Or use a publicly trusted certificate.

## Existing zotero.org users

A local library that was synced with zotero.org is tied to that account's
userID. On the first login to the on-premise server, Zotero detects the
different account and asks how to proceed: keep and upload the local data, or
reset it (see https://www.zotero.org/support/kb/switching_accounts). Try this
on a copy of the profile first. Files stored only on zotero.org must be
downloaded beforehand ("Download files at sync time" → "as needed" off).
