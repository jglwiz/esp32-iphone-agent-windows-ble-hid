#requires -Version 5.1
[CmdletBinding()]
param([string]$Port, [switch]$Upload, [switch]$SetupOnly)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem) {
    throw 'This script requires 64-bit Windows.'
}
if ($Upload -and ($SetupOnly -or [string]::IsNullOrWhiteSpace($Port))) {
    throw 'Upload requires an explicitly verified -Port and cannot be combined with -SetupOnly.'
}
$projectDir = Split-Path $PSScriptRoot -Parent
$repositoryDir = $projectDir
# ESP32 GCC constructs long, unnormalized include paths on Windows. A temporary
# drive alias keeps them short even when the clone lives in a deeply nested folder.
$drive = $null
$occupied = @([Environment]::GetLogicalDrives() | ForEach-Object { $_.Substring(0, 1) })
$occupied += @(Get-PSDrive -PSProvider FileSystem | Select-Object -ExpandProperty Name)
foreach ($letter in [char[]]'ZYXWVUTSRQPONMLKJIHGFED') {
    if ([string]$letter -in $occupied) { continue }
    & subst.exe "${letter}:" $projectDir
    if ($LASTEXITCODE -eq 0) { $drive = "${letter}:"; break }
}
if (-not $drive) { throw 'No free drive letter is available for the temporary build path.' }
try {
    $projectDir = "$drive\"
    $toolsDir = Join-Path $projectDir '.tools'
    $depsDir = Join-Path $projectDir '.build-deps'
    $outputDir = Join-Path $projectDir '.build'
    $cliVersion = '1.5.1'
    $coreVersion = '3.3.12'
    $cliDir = Join-Path $toolsDir "arduino-cli-$cliVersion"
    $cli = Join-Path $cliDir 'arduino-cli.exe'
    $indexUrl = 'https://espressif.github.io/arduino-esp32/package_esp32_index.json'
    $fqbn = 'esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=default,FlashSize=16M,PSRAM=opi,PartitionScheme=app3M_fat9M_16MB'

    function Get-VerifiedArchive([string]$Url, [string]$Path, [string]$Hash) {
        $displayPath = Join-Path $repositoryDir $Path.Substring(3)
        if (-not (Test-Path -LiteralPath $Path)) {
            Write-Host "Downloading $Url"
            $partial = "$Path.partial"
            Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $partial
            if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $Hash) {
                throw "Archive hash mismatch: $displayPath.partial. Remove this file and retry."
            }
            Move-Item -LiteralPath $partial -Destination $Path -Force
        }
        if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $Hash) {
            throw "Archive hash mismatch: $displayPath. Remove this file and retry."
        }
    }

    function Invoke-Arduino {
        & $cli --config-file $config @args
        if ($LASTEXITCODE -ne 0) { throw "Arduino CLI failed (exit $LASTEXITCODE): $args" }
    }

    New-Item -ItemType Directory -Force -Path $toolsDir, $depsDir | Out-Null
    $archive = Join-Path $toolsDir "arduino-cli-$cliVersion.zip"
    Get-VerifiedArchive "https://github.com/arduino/arduino-cli/releases/download/v$cliVersion/arduino-cli_${cliVersion}_Windows_64bit.zip" $archive 'FABE42E0EB04D00E776A66178299FF95A46C623DBC260F997E58FD514853DD40'
    if (-not (Test-Path -LiteralPath $cli)) {
        Expand-Archive -LiteralPath $archive -DestinationPath $cliDir -Force
    }

    # Isolate Arduino paths from the user's global installation; restore the environment on exit.
    $settings = @{
        ARDUINO_DIRECTORIES_DATA = (Join-Path $toolsDir 'arduino-data')
        ARDUINO_DIRECTORIES_DOWNLOADS = (Join-Path $toolsDir 'arduino-downloads')
        ARDUINO_DIRECTORIES_USER = (Join-Path $toolsDir 'arduino-user')
        ARDUINO_BOARD_MANAGER_ADDITIONAL_URLS = $indexUrl
    }
    $previous = @{}
    $config = Join-Path $toolsDir 'arduino-cli.yaml'
    Set-Content -LiteralPath $config -Value '{}' -Encoding ASCII
    try {
        foreach ($name in $settings.Keys) {
            $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
            [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process')
        }
        $installed = (Invoke-Arduino core list --format json | Out-String | ConvertFrom-Json)
        $core = @($installed.platforms | Where-Object { $_.id -eq 'esp32:esp32' -and $_.installed_version -eq $coreVersion })
        if ($core.Count -eq 0) {
            Invoke-Arduino core update-index
            Invoke-Arduino core install "esp32:esp32@$coreVersion"
        }

        $nimbleArchive = Join-Path $depsDir 'nimble-2.3.8.zip'
        Get-VerifiedArchive 'https://codeload.github.com/h2zero/NimBLE-Arduino/zip/refs/tags/2.3.8' $nimbleArchive '4849BA65F187EA237A5C8DE7FD0916692CFD058771EB8E8BDAB3135AEFAA78AF'
        Expand-Archive -LiteralPath $nimbleArchive -DestinationPath $depsDir -Force
        Write-Host "Toolchain ready: Arduino CLI $cliVersion, ESP32 $coreVersion, NimBLE-Arduino 2.3.8"
        if ($SetupOnly) { return }
        Invoke-Arduino compile --fqbn $fqbn --library (Join-Path $depsDir 'NimBLE-Arduino-2.3.8') --build-path (Join-Path $outputDir 'work') --output-dir $outputDir (Join-Path $projectDir 'firmware\serial_ble_hid')
        if ($Upload) {
            Invoke-Arduino upload --fqbn $fqbn --port $Port --input-dir $outputDir (Join-Path $projectDir 'firmware\serial_ble_hid')
        }
    } finally {
        foreach ($name in $previous.Keys) {
            [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
        }
    }
} finally {
    & subst.exe $drive /D
    if ($LASTEXITCODE -ne 0) { Write-Warning "Could not remove temporary drive mapping $drive" }
}
