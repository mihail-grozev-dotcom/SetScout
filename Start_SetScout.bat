@echo off
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python server.py
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        py server.py
    ) else (
        echo Python 3.10 or newer is required. Install Python and try again.
        pause
    )
)
