[CmdletBinding()]
param(
    [string]$Python = ".\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repositoryRoot $Python
$specPath = Join-Path $repositoryRoot "packaging\icp_renov_contracts.spec"
$resolvedRoot = [System.IO.Path]::GetFullPath($repositoryRoot)

if (!(Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Python de build introuvable : $pythonPath"
}

foreach ($name in @("build", "dist")) {
    $outputPath = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $name))
    if (!$outputPath.StartsWith($resolvedRoot + [System.IO.Path]::DirectorySeparatorChar)) {
        throw "Sortie de build hors dépôt refusée : $outputPath"
    }
    if (Test-Path -LiteralPath $outputPath) {
        Remove-Item -LiteralPath $outputPath -Recurse -Force
    }
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
