# Install the single per-user backend startup entry after migration acceptance.
$ErrorActionPreference = 'Stop'
$backendRoot = Split-Path -Parent $PSScriptRoot
$pythonWindowless = Join-Path $backendRoot '.venv\Scripts\pythonw.exe'
$deployTool = Join-Path $PSScriptRoot 'deploy.py'
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw 'Backend environment missing' }
if (-not (Test-Path -LiteralPath (Join-Path $backendRoot '.deployment.json'))) { throw 'Deployment configuration missing' }
$startupDirectory = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startupDirectory 'iPhone Backend.lnk'
$shellObject = New-Object -ComObject WScript.Shell
$shortcut = $shellObject.CreateShortcut($shortcutPath)
if ((Test-Path -LiteralPath $shortcutPath) -and $shortcut.TargetPath -ne $pythonWindowless) {
    throw 'An unrelated startup entry already uses this name'
}
$shortcut.TargetPath = $pythonWindowless
$shortcut.Arguments = '"' + $deployTool + '" start'
$shortcut.WorkingDirectory = $backendRoot
$shortcut.WindowStyle = 7
$shortcut.Description = 'Unified iPhone backend manager: ESP32 and AirPlay'
$shortcut.Save()
Write-Output $shortcutPath
