[CmdletBinding()]
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$iconPng = Join-Path $projectRoot "net_stability_icon_minimal.png"
$iconIco = Join-Path $projectRoot "net_stability_icon_minimal.ico"
$versionInfo = Join-Path $projectRoot "version_info.txt"
$entryScript = Join-Path $projectRoot "net_stability_gui.py"

Push-Location $projectRoot
try {
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name NetworkStabilityMonitor `
        --icon $iconIco `
        --add-data "$iconPng;." `
        --add-data "$iconIco;." `
        --version-file $versionInfo `
        --distpath .\dist `
        --workpath .\build\pyinstaller `
        --specpath .\build `
        $entryScript

    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    Write-Host "Built: $projectRoot\dist\NetworkStabilityMonitor.exe"
}
finally {
    Pop-Location
}
