param([string]$Port, [switch]$Upload)
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path $PSScriptRoot -Parent
$workspaceDir = Split-Path $projectDir -Parent
$env:ARDUINO_DIRECTORIES_DATA = Join-Path $workspaceDir '.tools\arduino-data'
$env:ARDUINO_DIRECTORIES_DOWNLOADS = Join-Path $workspaceDir '.tools\arduino-downloads'
$cli = Join-Path $workspaceDir '.tools\arduino-cli\arduino-cli.exe'
$fqbn = 'esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=default,FlashSize=16M,PSRAM=opi,PartitionScheme=app3M_fat9M_16MB'
$sketch = Join-Path $projectDir 'firmware\serial_ble_hid'
$outputDir = Join-Path $projectDir '.build'
# Use a pinned standalone NimBLE-Arduino library for the composite HID device.
$depsDir = Join-Path $projectDir '.build-deps'
New-Item -ItemType Directory -Force -Path $depsDir | Out-Null
$archive = Join-Path $depsDir 'nimble-2.3.8.zip'
if (-not (Test-Path -LiteralPath $archive)) {
    Invoke-WebRequest 'https://codeload.github.com/h2zero/NimBLE-Arduino/zip/refs/tags/2.3.8' -OutFile $archive
}
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne '4849BA65F187EA237A5C8DE7FD0916692CFD058771EB8E8BDAB3135AEFAA78AF') {
    throw 'NimBLE-Arduino archive hash mismatch'
}
Expand-Archive -LiteralPath $archive -DestinationPath $depsDir -Force
$nimble = Join-Path $depsDir 'NimBLE-Arduino-2.3.8'
& $cli compile --fqbn $fqbn --library $nimble --output-dir $outputDir $sketch
if ($LASTEXITCODE) { throw 'Firmware compile failed' }
if ($Upload) {
    if (-not $Port) { throw 'Upload requires an explicitly verified -Port' }
    & $cli upload --fqbn $fqbn --port $Port --input-dir $outputDir $sketch
    if ($LASTEXITCODE) { throw 'Firmware upload failed' }
}
