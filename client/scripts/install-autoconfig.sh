#!/usr/bin/env bash
# System-wide, locked configuration for a Zotero installation (Linux; on
# Windows copy the same two files into C:\Program Files\Zotero\ by GPO/SCCM).
#   sudo install-autoconfig.sh /opt/zotero zotero.example.org
set -euo pipefail
dir=${1:?usage: $0 <zotero-install-dir> <ZOTERO_HOST>}; host=${2:?}
src="$(dirname "$0")/../autoconfig"
[ -x "$dir/zotero-bin" ] || [ -f "$dir/zotero.exe" ] || { echo "not a Zotero install dir: $dir" >&2; exit 1; }
install -d "$dir/defaults/pref"
install -m 0644 "$src/defaults/pref/autoconfig.js" "$dir/defaults/pref/autoconfig.js"
sed "s/ZOTERO_HOST/$host/g" "$src/zotero.cfg" > "$dir/zotero.cfg"; chmod 0644 "$dir/zotero.cfg"
echo "installed AutoConfig into $dir"
