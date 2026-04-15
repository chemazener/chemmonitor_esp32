@echo off
title Server Monitor - Instalador Windows
cd /d "%~dp0"
echo ============================================
echo   ChemMonitor - Instalador para Windows
echo   by ChemaDev ^& ClaudeCode
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
echo title ChemMonitor Server >> start_monitor.bat
echo echo Iniciando ChemMonitor Server en puerto 8090... >> start_monitor.bat
echo echo Presiona Ctrl+C para detener >> start_monitor.bat
echo python server_monitor.py >> start_monitor.bat

:: Create VBS launcher (hidden, no console window)
echo Set oWS = WScript.CreateObject("WScript.Shell") > "%~dp0chemmonitor_server.vbs"
echo oWS.Run "cmd /c cd /d ""%~dp0"" && python server_monitor.py", 0, False >> "%~dp0chemmonitor_server.vbs"

:: Create startup shortcut
echo Set oWS = WScript.CreateObject("WScript.Shell") > "%TEMP%\cm_shortcut.vbs"
echo sLinkFile = oWS.SpecialFolders("Startup") ^& "\ChemMonitor Server.lnk" >> "%TEMP%\cm_shortcut.vbs"
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> "%TEMP%\cm_shortcut.vbs"
echo oLink.TargetPath = "wscript.exe" >> "%TEMP%\cm_shortcut.vbs"
echo oLink.Arguments = """%~dp0chemmonitor_server.vbs""" >> "%TEMP%\cm_shortcut.vbs"
echo oLink.WorkingDirectory = "%~dp0" >> "%TEMP%\cm_shortcut.vbs"
echo oLink.Description = "ChemMonitor Server" >> "%TEMP%\cm_shortcut.vbs"
echo oLink.Save >> "%TEMP%\cm_shortcut.vbs"
cscript //nologo "%TEMP%\cm_shortcut.vbs"
del "%TEMP%\cm_shortcut.vbs"

echo [OK] Creado start_monitor.bat
echo [OK] Servicio instalado en Inicio de Windows
echo.
echo ============================================
echo   Instalacion completada!
echo.
echo   El servidor arrancara automaticamente
echo   con Windows (sin ventana).
echo.
echo   Para ejecutar manualmente: start_monitor.bat
echo   Para detener: taskkill /F /IM python.exe
echo   Para desinstalar, borra:
echo     %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ChemMonitor Server.lnk
echo.
echo   Configura la IP de este PC en el ESP32
echo   desde: http://192.168.4.1
echo ============================================

:: Start now
echo.
echo Iniciando servidor ahora...
start "" wscript.exe "%~dp0chemmonitor_server.vbs"
echo [OK] Servidor ejecutandose en segundo plano

pause
