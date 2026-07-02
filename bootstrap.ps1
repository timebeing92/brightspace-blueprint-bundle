# Create the bundle's virtual environment and install dependencies (Windows).
# Run once after unzipping, from PowerShell, in the bundle folder:
#     ./bootstrap.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$py = "python"
& $py --version | Out-Null

if (-not (Test-Path ".venv")) {
    Write-Host "Creating .venv ..."
    & $py -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

Write-Host ""
Write-Host "Done. The environment is ready."
Write-Host "Next:  .\.venv\Scripts\python.exe scripts\build_blueprint_bundle.py C:\path\to\course-export.zip"
