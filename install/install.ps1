# NoCali installer for Windows.
# Copies the app to %APPDATA%\NoCali\app and starts it at login (Startup folder shortcut).
# Runs on http://localhost:7311.
#
#   powershell -ExecutionPolicy Bypass -File install\install.ps1            install or update
#   powershell -ExecutionPolicy Bypass -File install\install.ps1 uninstall  remove the login item

param([string]$Action = "install")
$ErrorActionPreference = "Stop"

$Src = Join-Path $PSScriptRoot "..\app" | Resolve-Path
$Data = Join-Path $env:APPDATA "NoCali"
$App = Join-Path $Data "app"
$Startup = [Environment]::GetFolderPath("Startup")
$Link = Join-Path $Startup "NoCali.lnk"

# stop a running instance
Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
  Where-Object { $_.CommandLine -like "*NoCali*server.py*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

if ($Action -eq "uninstall") {
  Remove-Item $Link -ErrorAction SilentlyContinue
  Write-Host "NoCali login item removed."
  exit 0
}

# find pythonw.exe (no console window)
$py = $null
foreach ($cmd in @("pythonw.exe", "pyw.exe")) {
  $c = Get-Command $cmd -ErrorAction SilentlyContinue
  if ($c) { $py = $c.Source; break }
}
if (-not $py) {
  Write-Host "NoCali needs Python 3.8+ from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')."
  exit 1
}

New-Item -ItemType Directory -Force -Path $App | Out-Null
Copy-Item (Join-Path $Src "*.py"), (Join-Path $Src "index.html") $App -Force
Write-Host "Installed to $App"

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($Link)
$sc.TargetPath = $py
$sc.Arguments = "`"$App\server.py`""
$sc.WorkingDirectory = $App
$sc.Description = "NoCali (Bambu Lab helper)"
$sc.Save()

Start-Process -FilePath $py -ArgumentList "`"$App\server.py`"" -WorkingDirectory $App
$ok = $false
for ($i = 0; $i -lt 10; $i++) {
  try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://localhost:7311/health | Out-Null; $ok = $true; break } catch { Start-Sleep 1 }
}
if ($ok) {
  Write-Host "NoCali is running: http://localhost:7311"
  Start-Process "http://localhost:7311"
} else {
  Write-Host "NoCali did not start. Try running: python `"$App\server.py`""
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Host "Optional: install ffmpeg for the live camera (winget install Gyan.FFmpeg)."
}
