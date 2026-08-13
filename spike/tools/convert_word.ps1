param(
  [Parameter(Mandatory=$true)][string]$InputDocx,
  [Parameter(Mandatory=$true)][string]$OutputPdf,
  [Parameter(Mandatory=$true)][string]$AttemptLog
)
$ErrorActionPreference = 'Stop'
$word = $null
$document = $null
$attemptId = [Guid]::NewGuid().ToString('N')
function Write-Stage([string]$Stage, [string]$Detail = '') {
  $line = '{0:o}`t{1}`t{2}`t{3}' -f [DateTime]::UtcNow, $attemptId, $Stage, $Detail
  Add-Content -LiteralPath $AttemptLog -Value $line -Encoding UTF8
}
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class IcpWordPid {
  [DllImport("user32.dll")]
  public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
}
'@
try {
  Write-Stage 'COM_CREATE_BEGIN'
  $word = New-Object -ComObject Word.Application
  Write-Stage 'COM_CREATE_OK'
  $word.Visible = $false
  $word.DisplayAlerts = 0
  $ownedPid = [uint32]0
  [void][IcpWordPid]::GetWindowThreadProcessId([IntPtr]$word.Hwnd, [ref]$ownedPid)
  Write-Stage 'OWNED_PID' ([string]$ownedPid)
  Write-Stage 'DOCUMENT_OPEN_BEGIN' $InputDocx
  $document = $word.Documents.Open($InputDocx, $false, $true, $false)
  Write-Stage 'DOCUMENT_OPEN_OK'
  Write-Stage 'REPAGINATE_BEGIN'
  $document.Repaginate()
  Write-Stage 'REPAGINATE_OK'
  $wdExportFormatPDF = 17
  $wdExportOptimizeForPrint = 0
  $wdExportAllDocument = 0
  $wdExportDocumentContent = 0
  $wdExportCreateNoBookmarks = 0
  Write-Stage 'PDF_EXPORT_BEGIN' $OutputPdf
  $document.ExportAsFixedFormat($OutputPdf, $wdExportFormatPDF, $false, $wdExportOptimizeForPrint, $wdExportAllDocument, 1, 1, $wdExportDocumentContent, $true, $true, $wdExportCreateNoBookmarks, $true, $true, $false)
  Write-Stage 'PDF_EXPORT_OK'
  if (-not (Test-Path -LiteralPath $OutputPdf)) { throw 'Word did not create the requested PDF.' }
}
catch {
  Write-Stage 'ERROR' $_.Exception.Message
  throw
}
finally {
  if ($document -ne $null) {
    Write-Stage 'DOCUMENT_CLOSE_BEGIN'
    $document.Close($false)
    Write-Stage 'DOCUMENT_CLOSE_OK'
  }
  if ($word -ne $null) {
    Write-Stage 'WORD_QUIT_BEGIN'
    $word.Quit()
    Write-Stage 'WORD_QUIT_OK'
  }
  if ($document -ne $null) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document) }
  if ($word -ne $null) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($word) }
  [GC]::Collect()
  [GC]::WaitForPendingFinalizers()
  Write-Stage 'ATTEMPT_COMPLETE'
}
