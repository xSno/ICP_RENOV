[CmdletBinding()]
param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [string]$InnoCompiler = "",
    [string]$SignTool = "",
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[A-Fa-f0-9]{40}$")]
    [string]$CertificateThumbprint,
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$bundleScript = Join-Path $repositoryRoot "packaging\build_windows_bundle.ps1"
$bundleExecutable = Join-Path $repositoryRoot "dist\ICP Renov - Contrats\ICP Renov - Contrats.exe"
$installerScript = Join-Path $repositoryRoot "packaging\installer\icp_renov_contrats.iss"
$installer = Join-Path $repositoryRoot "installer-output\ICP-Renov-Contrats-Setup-1.0.0.exe"
$CertificateThumbprint = $CertificateThumbprint.ToUpperInvariant()

function Resolve-SignTool {
    param([string]$RequestedPath)

    if ($RequestedPath) {
        if (!(Test-Path -LiteralPath $RequestedPath -PathType Leaf)) { throw "SignTool introuvable : $RequestedPath" }
        return (Resolve-Path -LiteralPath $RequestedPath).Path
    }

    $candidates = @(
        (Get-Command "signtool.exe" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
        (Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter "signtool.exe" -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } |
            Sort-Object FullName -Descending |
            Select-Object -First 1 -ExpandProperty FullName)
    )
    $resolved = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
    if (!$resolved) { throw "SignTool Windows SDK est requis pour une release signee." }
    return (Resolve-Path -LiteralPath $resolved).Path
}

function Invoke-SignTool {
    param([string[]]$Arguments, [string]$Action)

    & $script:resolvedSignTool @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Action a echoue (code $LASTEXITCODE)." }
}

function Assert-AuthenticodeSignature {
    param([string]$Artifact, [string]$Label)

    Invoke-SignTool -Arguments @("verify", "/pa", "/v", $Artifact) -Action "Verification SignTool de $Label"
    $signature = Get-AuthenticodeSignature -LiteralPath $Artifact
    if ($signature.Status -ne "Valid") { throw "Signature $Label invalide : $($signature.Status)." }
    if (!$signature.SignerCertificate -or $signature.SignerCertificate.Thumbprint -ne $CertificateThumbprint) {
        throw "Le signataire de $Label ne correspond pas au certificat ICP Renov selectionne."
    }
}

if (!(Test-Path -LiteralPath $bundleScript -PathType Leaf)) { throw "Script de bundle introuvable : $bundleScript" }
if (!(Test-Path -LiteralPath $installerScript -PathType Leaf)) { throw "Script Inno Setup introuvable : $installerScript" }

$certificate = Get-Item -LiteralPath "Cert:\CurrentUser\My\$CertificateThumbprint" -ErrorAction SilentlyContinue
if (!$certificate -or !$certificate.HasPrivateKey) { throw "Certificat de signature introuvable ou sans cle privee dans Cert:\CurrentUser\My." }
if ($certificate.Subject -ne "CN=ICP Renov") { throw "Le certificat de signature doit identifier ICP Renov." }
if (!($certificate.EnhancedKeyUsageList | Where-Object { $_.ObjectId.Value -eq "1.3.6.1.5.5.7.3.3" })) { throw "Le certificat selectionne ne possede pas l'EKU Code Signing." }
if ($certificate.NotAfter -le (Get-Date)) { throw "Le certificat de signature est expire." }

$resolvedSignTool = Resolve-SignTool -RequestedPath $SignTool

& $bundleScript -Python $Python
if ($LASTEXITCODE -ne 0) { throw "La construction du bundle a echoue (code $LASTEXITCODE)." }
if (!(Test-Path -LiteralPath $bundleExecutable -PathType Leaf)) { throw "Bundle one-dir incomplet : $bundleExecutable" }

Invoke-SignTool -Arguments @("sign", "/sha", $CertificateThumbprint, "/fd", "SHA256", "/tr", $TimestampUrl, "/td", "SHA256", $bundleExecutable) -Action "Signature de l'executable principal"
Assert-AuthenticodeSignature -Artifact $bundleExecutable -Label "l'executable principal"

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

& $InnoCompiler "/DSignToolPath=$resolvedSignTool" "/DCertificateThumbprint=$CertificateThumbprint" "/DTimestampUrl=$TimestampUrl" $installerScript
if ($LASTEXITCODE -ne 0) { throw "La compilation Inno Setup a echoue (code $LASTEXITCODE)." }
if (!(Test-Path -LiteralPath $installer -PathType Leaf)) { throw "Installeur attendu introuvable : $installer" }

Assert-AuthenticodeSignature -Artifact $installer -Label "l'installeur"
Write-Output "Installeur signe construit : $installer"
