@echo off
chcp 949 > nul
title 이로재로타로 - 제미나이 중계 서버
cd /d "%~dp0"
echo.
echo  중계 서버를 시작합니다. 이 창을 닫으면 중계가 멈춥니다.
echo.
TarotRelay.exe
echo.
echo  서버가 멈췄습니다. 아무 키나 누르면 창이 닫힙니다.
pause > nul
