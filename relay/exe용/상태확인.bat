@echo off
chcp 949 > nul
title 이로재로타로 중계서버 - 상태확인
echo.
echo  중계서버 상태를 확인합니다...
echo.

powershell -NoProfile -Command ^
  "try { $r = Invoke-RestMethod -Uri 'http://localhost:8765/health' -TimeoutSec 5; Write-Host '  [O] 중계서버 켜져 있음'; if ($r.public) { Write-Host ('      터널 주소: ' + $r.public) } else { Write-Host '      터널: 안 열림 (이 컴퓨터에서만 사용 가능)' }; Write-Host ('      오늘 처리한 요청: ' + $r.today + '건'); if ($r.connected) { Write-Host '      제미나이 창: 열려 있음' } else { Write-Host '      제미나이 창: 닫혀 있음 (정상)' } } catch { Write-Host '  [X] 중계서버가 꺼져 있습니다.'; Write-Host '      중계서버 실행.bat 을 눌러 켜주세요.' }"

echo.
tasklist /FI "IMAGENAME eq TarotRelay.exe" | find /I "TarotRelay.exe"
tasklist /FI "IMAGENAME eq ngrok.exe" | find /I "ngrok.exe"
echo.
pause
