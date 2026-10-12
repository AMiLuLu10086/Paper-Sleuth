param([switch]$CpuOnly, [switch]$SkipModels)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PIP_CACHE_DIR = Join-Path $PSScriptRoot '.cache\pip'
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required.' }
}
$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
function Install-ProjectPackages([string[]]$Arguments) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv pip install --python $pythonExe --cache-dir .cache/uv @Arguments
    } else {
        & $pythonExe -m ensurepip --upgrade
        & $pythonExe -m pip install @Arguments
    }
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
if ($CpuOnly) {
    Install-ProjectPackages -Arguments @('torch==2.11.0','torchvision==0.26.0','--index-url','https://download.pytorch.org/whl/cpu')
} else {
    Install-ProjectPackages -Arguments @('torch==2.11.0','torchvision==0.26.0','--index-url','https://download.pytorch.org/whl/cu128')
}
Install-ProjectPackages -Arguments @('-r','requirements.txt')
Push-Location -LiteralPath 'frontend'
try {
    & npm.cmd ci --cache ../.cache/npm
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
if (-not $SkipModels) {
    & $pythonExe scripts/download_models.py
    if ($LASTEXITCODE -ne 0) { throw 'Model download failed; retry install.ps1 to resume.' }
}
Write-Host 'Installation complete. Run .\start.ps1 to open PaperSleuth.'
