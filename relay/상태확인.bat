@echo off
chcp 949 > nul
title 이로재로타로 중계서버 - 상태확인
echo.
echo  중계서버 상태를 확인합니다...
echo.

powershell -NoProfile -Command ^
  "try { $r = Invoke-RestMethod -Uri 'http://localhost:8765/health' -TimeoutSec 5; Write-Host '  [O] 중계서버 켜져 있음'; if ($r.connected) { Write-Host '      제미나이 창: 열려 있음 (요청 처리 중이거나 방금 처리함)' } else { Write-Host '      제미나이 창: 닫혀 있음 (정상 - 요청 오면 자동으로 열립니다)' } } catch { Write-Host '  [X] 중계서버가 꺼져 있습니다.'; Write-Host '      중계서버 실행.bat 을 눌러 켜주세요.' }"

echo.
netstat -ano | findstr ":8765" | findstr LISTENING
echo.
pause
