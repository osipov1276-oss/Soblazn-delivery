@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title SOBLAZN Mini App

if not exist "bot_config.env" goto setup
findstr /B /C:"BOT_TOKEN=" "bot_config.env" >nul 2>&1
if errorlevel 1 goto setup
goto run

:setup
cls
echo ==============================================
echo      SOBLAZN MINI APP - FIRST START
echo ==============================================
echo.
echo Open BotFather, copy the API token and paste it here.
echo The token will be saved only on this computer.
echo.
set /p "TOKEN=BOT TOKEN: "
if "%TOKEN%"=="" (
  echo Token is empty.
  pause
  exit /b 1
)
>"bot_config.env" echo BOT_TOKEN=%TOKEN%
>>"bot_config.env" echo COURIER_CHAT_ID=-1004342107012
>>"bot_config.env" echo DELIVERY_FEE=1000
>>"bot_config.env" echo REQUIRE_TELEGRAM_AUTH=0

:run
cls
echo Starting SOBLAZN Mini App...
py -3.13 --version >nul 2>&1
if errorlevel 1 (
  echo Python 3.13 was not found.
  pause
  exit /b 1
)
py -3.13 -m pip install -r requirements.txt
if errorlevel 1 (
  echo Failed to install libraries.
  pause
  exit /b 1
)
echo.
echo Open in browser: http://127.0.0.1:8080
echo Orders will be sent to the Telegram courier group.
echo To stop, press Ctrl+C.
echo.
py -3.13 app.py
pause
