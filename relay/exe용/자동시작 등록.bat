@echo off
chcp 949 > nul
title 이로재로타로 중계서버 - 자동시작 등록
cd /d "%~dp0"

set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "VBS=%STARTUP%\이로재로타로_중계서버.vbs"

echo.
echo  부팅할 때 중계서버가 창 없이 자동으로 켜지도록 등록합니다.
echo  등록 위치: %STARTUP%
echo.

> "%VBS%" echo Set sh = CreateObject("WScript.Shell")
>> "%VBS%" echo sh.CurrentDirectory = "%CD%"
>> "%VBS%" echo sh.Run """%CD%\TarotRelay.exe""", 0, False

if exist "%VBS%" (
  echo  [O] 등록 완료. 이제 컴퓨터를 켜면 알아서 돕니다.
  echo      기록은 이 폴더의 relay.log 에 쌓입니다.
  echo.
  choice /C YN /M "지금 바로 한 번 켜둘까요"
  if errorlevel 2 goto :end
  start "" wscript.exe "%VBS%"
  echo  [O] 실행했습니다. 상태확인.bat 으로 확인해보세요.
) else (
  echo  [X] 등록 실패. 이 파일을 관리자 권한으로 다시 실행해보세요.
)

:end
echo.
pause
