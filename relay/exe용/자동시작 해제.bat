@echo off
chcp 949 > nul
title 이로재로타로 중계서버 - 자동시작 해제
set "VBS=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\이로재로타로_중계서버.vbs"

echo.
if exist "%VBS%" (
  del "%VBS%"
  echo  [O] 자동시작을 해제했습니다.
) else (
  echo  [-] 등록된 자동시작이 없습니다.
)
echo.
choice /C YN /M "지금 돌고 있는 중계서버도 끌까요"
if errorlevel 2 goto :end

taskkill /IM TarotRelay.exe /F > nul 2>&1
taskkill /IM ngrok.exe /F > nul 2>&1
echo  [O] 중계서버와 터널을 껐습니다.

:end
echo.
pause
