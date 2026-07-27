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
echo  이미 돌고 있는 서버도 끄려면 아래를 눌러주세요.
choice /C YN /M "지금 돌고 있는 중계서버도 끌까요"
if errorlevel 2 goto :end

for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8765" ^| findstr LISTENING') do (
  taskkill /PID %%p /F > nul 2>&1
  echo  [O] 중계서버(PID %%p)를 껐습니다.
)

:end
echo.
pause
