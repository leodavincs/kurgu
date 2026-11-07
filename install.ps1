# Kurgu installer for Windows (PowerShell 5.1 or 7). No admin rights, no Python or ffmpeg needed beforehand.
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/leodavincs/kurgu/main/install.ps1 | iex"
# Safe to run again: it upgrades. Override the package source with $env:KURGU_SOURCE (a path, URL or "git+https://...").
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is very slow with the progress bar on Windows PowerShell 5.1
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch {}

$Source = if ($env:KURGU_SOURCE) { $env:KURGU_SOURCE } else { 'https://github.com/leodavincs/kurgu/archive/refs/heads/main.zip' }
$Py = if ($env:KURGU_PYTHON) { $env:KURGU_PYTHON } else { '3.12' }

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function Run($exe, $argList) {
  & $exe @argList | Out-Host
  return $LASTEXITCODE
}
function Quiet($exe, $argList) {   # native command, stderr ignored (Windows PowerShell 5.1 turns stderr into errors otherwise)
  $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { return (& $exe @argList 2>$null) } finally { $ErrorActionPreference = $old }
}

$origPath = $env:Path

# 1. uv (installs and manages Python for us)
function Find-Uv {
  $c = Get-Command uv -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  foreach ($d in @("$env:USERPROFILE\.local\bin", "$env:USERPROFILE\.cargo\bin")) {
    if (Test-Path "$d\uv.exe") { $env:Path = "$d;$env:Path"; return "$d\uv.exe" }
  }
  return $null
}
$uv = Find-Uv
if (-not $uv) {
  Write-Host 'Installing uv (the Python manager) into your user folder...'
  try { Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression }
  catch { Fail "Could not download uv ($($_.Exception.Message)). Check your internet connection; behind a company proxy set HTTPS_PROXY (e.g. `$env:HTTPS_PROXY='http://proxy:8080'`) and run this again. Manual install: https://docs.astral.sh/uv/getting-started/installation/" }
  $uv = Find-Uv
  if (-not $uv) { Fail 'uv was installed but was not found. Close this window, open a new PowerShell and run the command again.' }
}

# 2. Kurgu itself, on its own managed Python (kurgu.exe and kurgu-notes.exe land in uv's tool bin folder)
Write-Host "Installing Kurgu (Python $Py is downloaded automatically if needed)..."
if ((Run $uv @('tool', 'install', '--force', '--reinstall', '--python', $Py, $Source)) -ne 0) {
  Fail "Installing Kurgu failed. Offline or behind a proxy? Set HTTPS_PROXY and retry. If the message above mentions Python, try: uv python install $Py"
}
$bin = Quiet $uv @('tool', 'dir', '--bin')
if (-not $bin) { $bin = "$env:USERPROFILE\.local\bin" }
$bin = "$bin".Trim()
$env:Path = "$bin;$env:Path"
$kurgu = Join-Path $bin 'kurgu.exe'
if (-not (Test-Path $kurgu)) { Fail "kurgu.exe was not found in $bin after installing. Run: uv tool list" }

# 3. ffmpeg (static build in %LOCALAPPDATA%\Kurgu\ffmpeg; skipped when one is already installed)
Write-Host 'Checking ffmpeg...'
if ((Run $kurgu @('setup-ffmpeg')) -ne 0) {
  Write-Host 'ffmpeg download failed (see above). Kurgu is installed; fix the network and run: kurgu setup-ffmpeg' -ForegroundColor Yellow
}

# 4. Self-check
if ((Run $kurgu @('doctor')) -ne 0) {
  Write-Host 'Some checks failed. Fix what is listed above, then run: kurgu doctor' -ForegroundColor Yellow
}

# 5. Make sure new terminals find `kurgu`
$null = Quiet $uv @('tool', 'update-shell')
if (-not (($origPath -split ';') | Where-Object { $_.TrimEnd('\') -ieq $bin.TrimEnd('\') })) {
  Write-Host ''
  Write-Host "Note: open a new PowerShell window so that the 'kurgu' command is found. Until then use: $kurgu"
}

Write-Host ''
Write-Host 'Done. Next step:  kurgu <your project folder>' -ForegroundColor Green
