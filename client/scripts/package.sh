#!/usr/bin/env bash
#
# Build pre-configured Zotero client packages from the OFFICIAL release
# binaries. Nothing is recompiled: the AutoConfig files (locked prefs pointing
# at the on-premise server) are added next to the binary. Automatic updates
# keep working, because updates don't remove unknown files.
#
#   client/scripts/package.sh <ZOTERO_HOST> [VERSION|latest] [OUTDIR]
#
# Produces in OUTDIR:
#   Zotero-<v>_linux-x86_64_<host>.tar.xz     unpack & run
#   Zotero-<v>_linux-arm64_<host>.tar.xz
#   Zotero-<v>_win-x64_<host>.zip             portable directory
#   Zotero-<v>_win-x64-installer-kit_<host>.zip   official setup.exe + install.ps1 (silent, for GPO/Intune/SCCM)
#   Zotero_macos-kit_<host>.zip               user.js + configure script (macOS: don't modify the signed .app)
#
set -euo pipefail
host=${1:?usage: $0 <ZOTERO_HOST> [VERSION|latest] [OUTDIR]}
version=${2:-latest}
out=$(realpath -m "${3:-dist}")
here=$(cd "$(dirname "$0")/.." && pwd)
dl=https://download.zotero.org/client/release

if [ "$version" = latest ]; then
	version=$(curl -fsSI "https://www.zotero.org/download/client/dl?channel=release&platform=linux-x86_64" \
		| sed -n 's#^location: .*/release/\([^/]*\)/.*#\1#ip' | tr -d '\r')
fi
echo "Zotero $version for https://$host/"
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
mkdir -p "$out"
tag="${host//[^A-Za-z0-9.-]/_}"

autoconfig() {  # autoconfig <install dir>
	bash "$here/scripts/install-autoconfig.sh" "$1" "$host" >/dev/null
}

for arch in x86_64 arm64; do
	echo "-- linux-$arch"
	curl -fsSL -o "$work/l.tar.xz" "$dl/$version/Zotero-${version}_linux-$arch.tar.xz"
	rm -rf "$work/l" && mkdir "$work/l" && tar -xJf "$work/l.tar.xz" -C "$work/l"
	autoconfig "$work/l/Zotero_linux-$arch"
	tar -cJf "$out/Zotero-${version}_linux-${arch}_${tag}.tar.xz" -C "$work/l" "Zotero_linux-$arch"
done

echo "-- win-x64 portable"
curl -fsSL -o "$work/w.zip" "$dl/$version/Zotero-${version}_win-x64.zip"
rm -rf "$work/w" && mkdir "$work/w" && (cd "$work/w" && unzip -q ../w.zip)
autoconfig "$work/w/Zotero_win-x64"
(cd "$work/w" && zip -qr "$out/Zotero-${version}_win-x64_${tag}.zip" Zotero_win-x64)

echo "-- win-x64 installer kit"
kit="$work/kit" && rm -rf "$kit" && mkdir -p "$kit/defaults/pref"
curl -fsSL -o "$kit/Zotero-${version}_x64_setup.exe" "$dl/$version/Zotero-${version}_x64_setup.exe"
cp "$here/autoconfig/defaults/pref/autoconfig.js" "$kit/defaults/pref/"
sed "s/ZOTERO_HOST/$host/g" "$here/autoconfig/zotero.cfg" > "$kit/zotero.cfg"
cat > "$kit/install.ps1" <<EOF
# Silent install of Zotero $version + on-premise configuration (run as administrator).
\$ErrorActionPreference = "Stop"
\$setup = Join-Path \$PSScriptRoot "Zotero-${version}_x64_setup.exe"
Start-Process -FilePath \$setup -ArgumentList "/S" -Wait
\$dir = Join-Path \$env:ProgramFiles "Zotero"
New-Item -ItemType Directory -Force -Path (Join-Path \$dir "defaults\\pref") | Out-Null
Copy-Item (Join-Path \$PSScriptRoot "defaults\\pref\\autoconfig.js") (Join-Path \$dir "defaults\\pref\\") -Force
Copy-Item (Join-Path \$PSScriptRoot "zotero.cfg") \$dir -Force
Write-Host "Zotero installed and configured for https://$host/"
EOF
(cd "$kit" && zip -qr "$out/Zotero-${version}_win-x64-installer-kit_${tag}.zip" .)

echo "-- macOS kit"
mk="$work/mac" && rm -rf "$mk" && mkdir -p "$mk/scripts"
cp "$here/user.js.template" "$mk/"
cp "$here/scripts/configure-profile.sh" "$mk/scripts/"
cat > "$mk/README.txt" <<EOF
1. Install the official Zotero for macOS: https://www.zotero.org/download/
2. Start Zotero once and quit it.
3. Run:  bash scripts/configure-profile.sh $host
4. Start Zotero -> Settings -> Sync -> Log In
EOF
(cd "$mk" && zip -qr "$out/Zotero_macos-kit_${tag}.zip" .)

(cd "$out" && sha256sum ./* > SHA256SUMS)
echo "done: $out"; ls -lh "$out"
