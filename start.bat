@echo off
REM Proxeen Assistant - Startup Script (Windows)

echo ===================================
echo Proxeen Assistant
echo ===================================
echo.

REM Check if .env exists
if not exist "backend\.env" (
    echo Warning: backend\.env not found
    echo Copying .env.example to .env...
    copy backend\.env.example backend\.env
    echo.
    echo ERROR: Please edit backend\.env with your API keys before running!
    echo notepad backend\.env
    pause
    exit /b 1
)

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python not found
    echo Please install Python 3.11 or higher
    pause
    exit /b 1
)

REM Check Node.js
node --version >nul 2>&1
if errorlevel 1 (
    echo Error: Node.js not found
    echo Please install Node.js 18 or higher
    pause
    exit /b 1
)

echo [OK] Python found
echo [OK] Node.js found
echo.

REM Install Python dependencies
if not exist "backend\venv" (
    echo Creating Python virtual environment...
    cd backend
    python -m venv venv
    call venv\Scripts\activate.bat
    echo Installing Python dependencies...
    pip install -r requirements.txt
    cd ..
) else (
    echo Activating Python virtual environment...
    cd backend
    call venv\Scripts\activate.bat
    cd ..
)

REM Install Node dependencies
if not exist "overlay\node_modules" (
    echo Installing Node.js dependencies...
    cd overlay
    call npm install
    cd ..
)

echo.
echo ===================================
echo Starting Proxeen Assistant...
echo ===================================
echo.

REM Start backend
echo Starting backend server...
cd backend
start "AI Assistant Backend" python server.py
cd ..

REM Wait for backend to be ready
echo Waiting for backend to be ready...
timeout /t 3 /nobreak >nul

REM Start overlay
echo Starting overlay...
cd overlay
start "AI Assistant Overlay" npm start
cd ..

echo.
echo [OK] Proxeen Assistant is running!
echo.
echo Keyboard shortcuts:
echo   Ctrl+Shift+A - Toggle overlay
echo   Ctrl+Shift+Q - Quit
echo.
echo Close this window to stop all services
echo.
pause
