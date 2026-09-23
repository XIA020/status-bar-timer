# fix_ps1_bom.ps1 - ensure a .ps1 is UTF-8 WITH BOM so PowerShell 5.1 decodes it as UTF-8.
# ASCII only, no Chinese literals (would itself be mis-decoded).
param([string[]]$Files)
$ErrorActionPreference = 'Stop'

foreach ($f in $Files) {
    if (-not (Test-Path $f)) { Write-Host "MISSING $f"; continue }
    $bytes = [System.IO.File]::ReadAllBytes($f)
    $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    Write-Host ("{0}  size={1}  bom={2}" -f (Split-Path $f -Leaf), $bytes.Length, $hasBom)
    if ($hasBom) { continue }

    # decode as UTF-8 (content is valid UTF-8), then rewrite WITH BOM
    $text = [System.Text.Encoding]::UTF8.GetString($bytes)
    $enc = New-Object System.Text.UTF8Encoding($true)
    [System.IO.File]::WriteAllText($f, $text, $enc)
    Write-Host ("  -> rewritten with BOM, size={0}" -f ([System.IO.File]::ReadAllBytes($f)).Length)
}
