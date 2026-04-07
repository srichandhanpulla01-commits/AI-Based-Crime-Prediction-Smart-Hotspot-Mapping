@echo off
setlocal
cd /d "%~dp0"
start "" http://127.0.0.1:5000
"C:\Users\Sri Chandhan\AppData\Local\Programs\Python\Python313\python.exe" app.py
