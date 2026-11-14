#!/usr/bin/env bash
# Write the on-premise settings into every Zotero profile of the current user
# (Linux and macOS).   Usage: configure-profile.sh zotero.example.org
set -euo pipefail
host=${1:?usage: $0 <ZOTERO_HOST>}
tmpl="$(dirname "$0")/../user.js.template"
case "$(uname)" in
	Darwin) base="$HOME/Library/Application Support/Zotero/Profiles" ;;
	*)      base="$HOME/.zotero/zotero" ;;
esac
if [ ! -d "$base" ]; then
	echo "No Zotero profile found in $base -- start Zotero once, quit it, and re-run." >&2
	exit 1
fi
for p in "$base"/*/; do
	[ -f "$p/prefs.js" ] || continue
	sed "s/ZOTERO_HOST/$host/g" "$tmpl" > "$p/user.js"
	echo "configured $p"
done
echo "Restart Zotero, then: Settings → Sync → Log In."
