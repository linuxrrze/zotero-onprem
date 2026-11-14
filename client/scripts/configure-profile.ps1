# Write the on-premise settings into every Zotero profile of the current user (Windows).
#   powershell -ExecutionPolicy Bypass -File configure-profile.ps1 zotero.example.org
param([Parameter(Mandatory = $true)][string]$ZoteroHost)
$tmpl = Join-Path $PSScriptRoot "..\user.js.template"
$base = Join-Path $env:APPDATA "Zotero\Zotero\Profiles"
if (-not (Test-Path $base)) { Write-Error "No Zotero profile in $base - start Zotero once, quit it, re-run."; exit 1 }
Get-ChildItem $base -Directory | Where-Object { Test-Path (Join-Path $_.FullName "prefs.js") } | ForEach-Object {
    (Get-Content $tmpl -Raw) -replace "ZOTERO_HOST", $ZoteroHost |
        Set-Content -Encoding UTF8 (Join-Path $_.FullName "user.js")
    Write-Host "configured $($_.FullName)"
}
Write-Host "Restart Zotero, then: Settings -> Sync -> Log In."
