@echo off
chcp 949 > nul
title 이로재로타로 - 제미나이 중계 서버
cd /d "%~dp0"
echo.
echo  이로재로타로 제미나이 중계 서버를 시작합니다.
echo  (창을 닫으면 중계가 멈춥니다. 항상 켜두세요)
echo.
python tarot_relay.py
echo.
echo  서버가 멈췄습니다. 아무 키나 누르면 창이 닫힙니다.
pause > nul
