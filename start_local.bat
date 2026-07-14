@echo off
chcp 65001 >nul
py -3.13 -m pip install -r requirements.txt
set PORT=8080
py -3.13 app.py
pause
