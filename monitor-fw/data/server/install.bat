@echo off
title Server Monitor - Instalador Windows
cd /d "%~dp0"
echo ============================================
echo   Server Monitor - Instalador para Windows
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python no encontrado. Instala Python 3.10+ desde:
    echo         https://www.python.org/downloads/
    echo.
    echo Asegurate de marcar "Add Python to PATH" durante la instalacion.
    pause
    exit /b 1
)

echo [OK] Python encontrado
python --version
echo.

:: Install dependencies
echo Instalando dependencias...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] Fallo al instalar dependencias
    pause
    exit /b 1
)
echo [OK] Dependencias instaladas
echo.

:: Get IP
echo Direccion IP de este equipo:
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4" ^| findstr /v "127.0.0"') do (
    echo   %%a
)
echo.

:: Create start script
echo @echo off > start_monitor.bat
echo cd /d "%%~dp0" >> start_monitor.bat
echo title Server Monitor >> start_monitor.bat
echo echo Iniciando Server Monitor en puerto 8090... >> start_monitor.bat
echo echo Presiona Ctrl+C para detener >> start_monitor.bat
echo python server_monitor.py >> start_monitor.bat

echo [OK] Creado start_monitor.bat
echo.
echo ============================================
echo   Instalacion completada!
echo.
echo   Para iniciar el monitor ejecuta:
echo     start_monitor.bat
echo.
echo   Luego configura la IP de este PC en el
echo   ESP32 desde: http://192.168.4.1
echo ============================================
pause
