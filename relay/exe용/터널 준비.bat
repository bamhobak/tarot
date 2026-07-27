@echo off
chcp 949 > nul
title 이로재로타로 중계서버 - 터널 준비
cd /d "%~dp0"

echo.
echo ================================================================
echo   터널 준비  (다른 컴퓨터나 폰에서도 제미나이 풀이를 쓰게 만들기)
echo ================================================================
echo.
echo   인증토큰과 주소는 이미 들어 있습니다. 여기서는 두 가지만 합니다.
echo     1) 백신 예외 등록
echo     2) ngrok 내려받기
echo.
echo ----------------------------------------------------------------
echo  [1/2] 백신 예외 등록
echo.
echo   윈도우 디펜더는 ngrok 을 원격접속 도구로 보고 지워버립니다.
echo   그래서 이 폴더를 검사 예외로 등록해야 합니다.
echo   (이 폴더 하나만 예외가 됩니다. 나머지 보호는 그대로입니다)
echo.
choice /C YN /M "  예외로 등록할까요"
if errorlevel 2 goto :skipdef

powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','%CD%\_백신예외.ps1','%CD%'"
echo.
:skipdef

echo ----------------------------------------------------------------
echo  [2/2] ngrok 내려받기 (33MB)
echo.
if exist ngrok.exe (
  echo   [O] 이미 있습니다.
  goto :done
)
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; Invoke-WebRequest -Uri 'https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip' -OutFile 'ngrok.zip'; Expand-Archive -Path 'ngrok.zip' -DestinationPath '.' -Force; Remove-Item 'ngrok.zip' -Force"
if not exist ngrok.exe (
  echo   [X] 실패. 백신이 지웠거나 인터넷이 막혔습니다.
  echo       백신 예외를 먼저 등록하고 다시 실행해주세요.
  pause
  exit /b 1
)
echo   [O] 내려받기 완료.

:done
echo.
echo ================================================================
echo   준비 끝났습니다.
echo.
echo   "자동시작 해제.bat" 으로 껐다가
echo   "중계서버 실행.bat" 으로 다시 켜면 터널이 함께 열립니다.
echo   열린 주소는 "상태확인.bat" 에서 볼 수 있습니다.
echo ================================================================
echo.
pause
