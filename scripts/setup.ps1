# Install the backend in this checkout without starting services or flashing hardware.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$UxPlayDir,
    [string]$Python = 'python',
    [string]$PhoneId = ''
)
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$backendDirectory = Join-Path $projectDirectory 'backend'
$environmentPython = Join-Path $backendDirectory '.venv\Scripts\python.exe'
$receiverDirectory = (Resolve-Path -LiteralPath $UxPlayDir).Path
if (-not (Test-Path -LiteralPath (Join-Path $receiverDirectory 'uxplay-windows.exe') -PathType Leaf)) {
    throw 'UxPlayDir must contain uxplay-windows.exe and its runtime dependencies'
}
if (-not (Test-Path -LiteralPath $environmentPython)) {
    & $Python -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required' }
    & $Python -m venv (Join-Path $backendDirectory '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
}
& $environmentPython -m pip install -e $backendDirectory
if ($LASTEXITCODE -ne 0) { throw 'Backend installation failed; configuration was not changed' }
$configurationPath = Join-Path $backendDirectory '.deployment.json'
if (Test-Path -LiteralPath $configurationPath) {
    Write-Output 'Existing deployment configuration retained. UxPlayDir/PhoneId were not applied.'
} else {
    $initializationArguments = @((Join-Path $backendDirectory 'tools\bootstrap.py'), 'init', '--uxplay-dir', $receiverDirectory)
    if ($PhoneId) { $initializationArguments += @('--phone-id', $PhoneId) }
    & $environmentPython @initializationArguments
    if ($LASTEXITCODE -ne 0) { throw 'Deployment initialization failed' }
}
Write-Output 'Backend installed. Follow doc/user/deployment.md for pairing, startup and acceptance.'
