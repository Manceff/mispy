<#
  Mispy — installer for Windows.

  Everything lands under your own user profile. No administrator rights, no
  driver, no service, nothing registered with Azure AD. If your company has
  locked the machine down, that is the point: this installs the way a portable
  app installs.

    .\install.ps1                 ask which speech model to use
    .\install.ps1 -Model small    pick it up front
    .\install.ps1 -NoModel        skip the model download for now
#>
[CmdletBinding()]
param(
  [string]$Model = "",
  [switch]$NoModel,
  [switch]$NoPath
)
$ErrorActionPreference = "Stop"

$Root    = $env:LOCALAPPDATA + "\Mispy"
$Lib     = "$Root\lib"
$BinDir  = "$Root\bin"
$Models  = "$Root\models"
$Config  = $env:APPDATA + "\Mispy"
$Src     = Split-Path -Parent $MyInvocation.MyCommand.Path

function Head($t) { Write-Host ""; Write-Host "> $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "  [ok] $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  [!]  $t" -ForegroundColor Yellow }
function Die($t)  { Write-Host "  [x]  $t" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "Mispy" -ForegroundColor White
Write-Host "  records your meetings and hands you back the minutes."

# ----------------------------------------------------------------- windows ----
Head "Checking this PC"
$os = [System.Environment]::OSVersion.Version
if ($os.Major -lt 10) { Die "Windows 10 or later is needed." }
Ok ("Windows " + $os.Major + "." + $os.Build + " on " + $env:PROCESSOR_ARCHITECTURE)

# ------------------------------------------------------------------ python ----
Head "Python"
$PyExe = $null
$PyArgs = @()
$VersionTest = "import sys; sys.exit(0 if sys.version_info[:2] >= (3,11) else 1)"
foreach ($candidate in @(@("py", "-3.13"), @("py", "-3.12"), @("py", "-3.11"), @("py", "-3"),
                         @("python3"), @("python"))) {
  $found = Get-Command $candidate[0] -ErrorAction SilentlyContinue
  if (-not $found) { continue }
  $pre = @()
  if ($candidate.Count -gt 1) { $pre = @($candidate[1]) }
  & $found.Source @pre -c $VersionTest 2>$null
  if ($LASTEXITCODE -eq 0) { $PyExe = $found.Source; $PyArgs = $pre; break }
}
if (-not $PyExe) {
  Warn "no Python 3.11 or later was found"
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    Write-Host "  installing Python for your user only (no admin needed)..."
    winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
    $found = Get-Command py -ErrorAction SilentlyContinue
    if (-not $found) { Die "Python is installed, but the py launcher is not on the PATH yet. Open a new terminal and run this again." }
    $PyExe = $found.Source
    $PyArgs = @("-3.12")
  } else {
    Die "install Python 3.11+ from https://python.org (tick 'Install for me only'), then run this again"
  }
}
$pyVersion = (& $PyExe @PyArgs -c "import sys; print(sys.version.split()[0])").Trim()
Ok "Python $pyVersion"

# ------------------------------------------------------------------- mispy ----
Head "Installing Mispy"
New-Item -ItemType Directory -Force -Path $Lib, $BinDir, $Models, $Config | Out-Null
if (Test-Path "$Lib\mispy")  { Remove-Item -Recurse -Force "$Lib\mispy" }
if (Test-Path "$Lib\style")  { Remove-Item -Recurse -Force "$Lib\style" }
Copy-Item -Recurse "$Src\mispy" "$Lib\mispy"
Copy-Item -Recurse "$Src\style" "$Lib\style"
Get-ChildItem -Recurse -Force -Directory -Filter "__pycache__" $Lib |
  ForEach-Object { Remove-Item -Recurse -Force $_.FullName }
Ok $Lib

$venv = "$Lib\.venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
  & $PyExe @PyArgs -m venv $venv
}
$vpy = "$venv\Scripts\python.exe"
& $vpy -m pip install --quiet --upgrade pip
Write-Host "  installing packages (this is the slow part)..."
& $vpy -m pip install --quiet -r "$Src\requirements.txt"
if ($LASTEXITCODE -ne 0) { Die "pip could not install the dependencies — see the output above" }
# so that `python -m mispy.capture_win`, the detached recorder, can find the package
$site = (& $vpy -c "import sysconfig; print(sysconfig.get_paths()['purelib'])").Trim()
Set-Content -Path "$site\mispy.pth" -Value $Lib -Encoding ASCII
Ok "python packages"

foreach ($m in @("soundcard", "numpy", "faster_whisper", "win32com.client")) {
  & $vpy -c "import $m" 2>$null
  if ($LASTEXITCODE -eq 0) { Ok $m.Split(".")[0] } else { Warn "$m did not import — Mispy will tell you what it cannot do" }
}

# ------------------------------------------------------------------ command ----
Head "Command"
$cmd = @"
@echo off
"$vpy" -c "import sys; sys.path.insert(0, r'$Lib'); from mispy import cli; sys.exit(cli.main(sys.argv))" %*
"@
Set-Content -Path "$BinDir\mispy.cmd" -Value $cmd -Encoding ASCII
Ok "mispy"

# ------------------------------------------------------------------ bouton ----
Head "The button in your notification area"
$trayCmd = @"
@echo off
start "" "$venv\Scripts\pythonw.exe" -c "import sys; sys.path.insert(0, r'$Lib'); from mispy import tray; sys.exit(tray.run())"
"@
Set-Content -Path "$BinDir\mispy-tray.cmd" -Value $trayCmd -Encoding ASCII
Ok "mispy-tray"

# Un raccourci dans le menu Démarrer : l'utilisateur ne devrait jamais avoir à
# retrouver un .cmd dans AppData pour lancer son enregistreur.
try {
  $startMenu = [Environment]::GetFolderPath("Programs")
  $shell = New-Object -ComObject WScript.Shell
  $lnk = $shell.CreateShortcut((Join-Path $startMenu "Mispy.lnk"))
  $lnk.TargetPath = "$BinDir\mispy-tray.cmd"
  $lnk.WorkingDirectory = $BinDir
  $lnk.WindowStyle = 7                       # réduit : pas de fenêtre noire au lancement
  $lnk.Description = "Mispy - records your meetings and writes the minutes"
  $lnk.Save()
  Ok "shortcut in the Start menu"
  if ([System.Environment]::UserInteractive) {
    $answer = Read-Host "  Start it automatically when you log in? [Y/n]"
    if ($answer -notmatch '^[nN]') {
      $startup = [Environment]::GetFolderPath("Startup")
      $auto = $shell.CreateShortcut((Join-Path $startup "Mispy.lnk"))
      $auto.TargetPath = "$BinDir\mispy-tray.cmd"
      $auto.WorkingDirectory = $BinDir
      $auto.WindowStyle = 7
      $auto.Save()
      Ok "it will be there every morning"
    }
  }
} catch {
  Warn "could not create the shortcut ($($_.Exception.Message)) - run mispy-tray by hand"
}

if (-not $NoPath) {
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  if ($userPath -notlike "*$BinDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$BinDir", "User")
    Warn "added $BinDir to your PATH — open a new terminal for it to take effect"
  } else {
    Ok "$BinDir is on your PATH"
  }
}

# ------------------------------------------------------------------- model ----
if (-not $NoModel) {
  Head "Speech model (runs on this PC, nothing is uploaded)"
  if (-not $Model) {
    Write-Host ""
    Write-Host "    1  large-v3-turbo   1.6 GB  - the right default on a work laptop" -ForegroundColor Gray
    Write-Host "    2  large-v3         3.1 GB  - best, but several times slower on a CPU" -ForegroundColor Gray
    Write-Host "    3  medium           1.5 GB  - noticeably weaker on accents" -ForegroundColor Gray
    Write-Host "    4  small            0.5 GB  - only if disk space is tight" -ForegroundColor Gray
    Write-Host ""
    $choice = Read-Host "  Your choice [1]"
    switch ($choice) { "2" { $Model = "large-v3" } "3" { $Model = "medium" } "4" { $Model = "small" } default { $Model = "large-v3-turbo" } }
  }
  if (-not (Test-Path "$Config\config.toml")) {
    Set-Content -Path "$Config\config.toml" -Encoding UTF8 -Value @"
[transcribe]
model = "$Model"
"@
  }
  Write-Host "  downloading $Model ..."
  & $vpy -c @"
import sys
try:
    from faster_whisper import WhisperModel
    WhisperModel('$Model', device='cpu', compute_type='int8', download_root=r'$Models')
    print('ok')
except Exception as exc:
    print('later:', exc); sys.exit(0)
"@
  Ok "$Model is on this machine"
}

# ---------------------------------------------------------------- tectonic ----
Head "PDF (optional)"
if (Get-Command tectonic -ErrorAction SilentlyContinue) {
  Ok "tectonic is installed — minutes will also be typeset as a PDF"
} else {
  Warn "tectonic is not installed: you get Markdown and Word, which is usually what you want at work"
  Write-Host "       if you want PDFs too:  winget install TectonicTypesetting.Tectonic" -ForegroundColor Gray
}

Write-Host ""
Write-Host "Installed." -ForegroundColor White
Write-Host ""
Write-Host "  Open a NEW terminal, then run:  mispy setup" -ForegroundColor Cyan
Write-Host "  Five minutes, once. Then it is one click from the Start menu:"
Write-Host "  look for Mispy, and a dot appears next to your clock." 
Write-Host ""
