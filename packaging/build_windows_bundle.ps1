[CmdletBinding()]
param(
    [string]$Python = ".\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repositoryRoot $Python
$specPath = Join-Path $repositoryRoot "packaging\icp_renov_contracts.spec"

if (!(Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Python de build introuvable : $pythonPath"
}

Push-Location $repositoryRoot
try {
    & $pythonPath -m PyInstaller --noconfirm --clean $specPath
    if ($LASTEXITCODE -ne 0) {
        throw "La construction PyInstaller a échoué (code $LASTEXITCODE)."
    }
}
finally {
    Pop-Location
}
