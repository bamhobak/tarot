@echo off
chcp 949 > nul
title 이로재로타로 중계서버 - 최초 설치
cd /d "%~dp0"
echo.
echo ================================================
echo   이로재로타로 제미나이 중계서버 - 최초 설치
echo ================================================
echo.

python --version > nul 2>&1
if errorlevel 1 (
  echo  [X] 파이썬이 없습니다.
  echo      https://www.python.org/downloads/ 에서 파이썬을 먼저 설치하세요.
  echo      설치할 때 "Add python.exe to PATH" 체크를 꼭 해주세요.
  echo.
  pause
  exit /b 1
)
for /f "delims=" %%v in ('python --version') do echo  [1/3] 파이썬 확인: %%v

echo  [2/3] playwright 설치 중... (처음이면 1~2분)
python -m pip install --upgrade pip > nul 2>&1
python -m pip install playwright
if errorlevel 1 (
  echo  [X] playwright 설치 실패. 인터넷 연결을 확인하세요.
  pause
  exit /b 1
)

echo  [3/3] 무창 크롬(headless shell) 내려받는 중... (100MB 정도)
python -m playwright install chromium-headless-shell
if errorlevel 1 (
  echo      chromium-headless-shell 이 없어서 chromium 으로 받습니다.
  python -m playwright install chromium
)

echo.
echo ================================================
echo   설치 끝났습니다.
echo.
echo   1) "중계서버 실행.bat" 을 눌러 서버를 켜세요.
echo   2) 이 PC가 항상 켜져 있다면 "자동시작 등록.bat" 도 한 번 눌러두세요.
echo      (부팅할 때마다 알아서 켜집니다)
echo ================================================
echo.
pause
