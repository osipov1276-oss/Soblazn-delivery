@echo off
cd /d "%~dp0"
if exist "bot_config.env" del /q "bot_config.env"
echo Saved token removed. Start the app again to enter a new token.
pause
