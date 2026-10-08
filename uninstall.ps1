<#
  Removes Mispy. Your minutes are never touched: they live in the folder you
  chose, and this script does not go near it.
#>
$Root = $env:LOCALAPPDATA + "\Mispy"
$Config = $env:APPDATA + "\Mispy"
$BinDir = "$Root\bin"

Write-Host ""
Write-Host "Removing Mispy."
Get-Process -Name python -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -like "$Root*" } | Stop-Process -Force -ErrorAction SilentlyContinue

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -like "*$BinDir*") {
  $clean = ($userPath.Split(";") | Where-Object { $_ -and $_ -ne $BinDir }) -join ";"
  [Environment]::SetEnvironmentVariable("Path", $clean, "User")
  Write-Host "  [ok] removed from your PATH"
}
if (Test-Path $Root) {
  $size = "{0:N1} GB" -f ((Get-ChildItem -Recurse -Force $Root | Measure-Object Length -Sum).Sum / 1GB)
  Remove-Item -Recurse -Force $Root
  Write-Host "  [ok] program and speech model removed ($size)"
}
if (Test-Path $Config) {
  $answer = Read-Host "  Remove your settings and glossary too? [y/N]"
  if ($answer -eq "y") { Remove-Item -Recurse -Force $Config; Write-Host "  [ok] settings removed" }
  else { Write-Host "  .    kept: $Config" }
}
Write-Host ""
Write-Host "Done. Your minutes were not touched."
Write-Host ""
