param([string]$Folder)
try {
  Add-MpPreference -ExclusionPath $Folder -ErrorAction Stop
  Add-MpPreference -ExclusionProcess 'ngrok.exe' -ErrorAction Stop
  Write-Host '  [O] 예외 등록 완료:' $Folder
} catch {
  Write-Host '  [X] 예외 등록 실패:' $_
}
Start-Sleep -Seconds 2
