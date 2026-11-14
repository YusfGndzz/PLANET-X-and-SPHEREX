@echo off
rem Harman - SPHEREx Zaman Tuneli sunucusunu baslatir. Bu pencere acik kaldigi surece site calisir.
cd /d "%~dp0"
title Harman sunucusu - KAPATMAYIN
echo Sunucu basliyor... Tarayicida su adresi acin: http://127.0.0.1:8000
echo Durdurmak icin bu pencereyi kapatin.
echo.
start "" /b cmd /c "timeout /t 4 /nobreak >nul & start http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn api.main:app --host 127.0.0.1 --port 8000
echo.
echo Sunucu durdu. Yukaridaki hata mesajina bakin.
pause
