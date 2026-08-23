[CmdletBinding()]
param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [string]$InnoCompiler = ""
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$bundleScript = Join-Path $repositoryRoot "packaging\build_windows_bundle.ps1"
$bundleExecutable = Join-Path $repositoryRoot "dist\ICP Renov - Contrats\ICP Renov - Contrats.exe"
$installerScript = Join-Path $repositoryRoot "packaging\installer\icp_renov_contrats.iss"

if (!(Test-Path -LiteralPath $bundleScript -PathType Leaf)) { throw "Script de bundle introuvable : $bundleScript" }
if (!(Test-Path -LiteralPath $installerScript -PathType Leaf)) { throw "Script Inno Setup introuvable : $installerScript" }

& $bundleScript -Python $Python
if ($LASTEXITCODE -ne 0) { throw "La construction du bundle a échoué (code $LASTEXITCODE)." }
if (!(Test-Path -LiteralPath $bundleExecutable -PathType Leaf)) { throw "Bundle one-dir incomplet : $bundleExecutable" }

if (!$InnoCompiler) {
    $candidates = @(
        (Get-Command "ISCC.exe" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 7\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 7\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 7\ISCC.exe")
    )
    $InnoCompiler = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
}
if (!$InnoCompiler) { throw "Inno Setup 7 est requis pour construire l'installeur Windows." }

& $InnoCompiler $installerScript
if ($LASTEXITCODE -ne 0) { throw "La compilation Inno Setup a échoué (code $LASTEXITCODE)." }

$installer = Join-Path $repositoryRoot "installer-output\ICP-Renov-Contrats-Setup-0.1.0.exe"
if (!(Test-Path -LiteralPath $installer -PathType Leaf)) { throw "Installeur attendu introuvable : $installer" }
Write-Output "Installeur construit : $installer"
