@echo off
cd /d %~dp0

echo ============================================
echo   Phone Farm - Instagram ^& TikTok Setup
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)
echo [OK] Python found.

:: Create virtual environment
if not exist venv (
    echo [INFO] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created.
) else (
    echo [OK] Virtual environment already exists.
)

:: Activate and install dependencies
echo [INFO] Installing dependencies...
call venv\Scripts\activate.bat
pip install --upgrade pip -q
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo [OK] Dependencies installed.

:: Create .env file if missing
if not exist .env (
    echo [INFO] Creating .env file...
    (
        echo SECRET_KEY=change-me-to-a-random-secret
        echo SERVER_URL=http://localhost:5000
        echo DATABASE_URL=sqlite:///instagram_farm.db
    ) > .env
    echo [OK] .env file created. Edit it to set your SERVER_URL and SECRET_KEY.
) else (
    echo [OK] .env file already exists.
)

:: Create uploads folder if missing
if not exist uploads (
    mkdir uploads
    echo [OK] uploads folder created.
)

:: Run database migrations
echo [INFO] Running database migrations...
set FLASK_APP=run.py
flask db upgrade
if errorlevel 1 (
    echo [WARN] flask db upgrade failed. Attempting db init...
    flask db init
    flask db migrate -m "initial"
    flask db upgrade
)
echo [OK] Database ready.

echo.
echo ============================================
echo   Installation complete!
echo   Run start.bat to launch the server.
echo ============================================
echo.
pause
