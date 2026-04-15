@echo off
cd /d "%~dp0"
title ChemMonitor USB - Instalar servicio

echo ============================================
echo   ChemMonitor USB - Instalar como servicio
echo ============================================
echo.

:: Create a VBS script to run Python hidden (no window)
echo Set oWS = WScript.CreateObject("WScript.Shell") > "%~dp0chemmonitor_launcher.vbs"
echo oWS.Run "python ""%~dp0chemmonitor_usb.py""", 0, False >> "%~dp0chemmonitor_launcher.vbs"

:: Create startup shortcut
echo Set oWS = WScript.CreateObject("WScript.Shell") > "%TEMP%\create_shortcut.vbs"
echo sLinkFile = oWS.SpecialFolders("Startup") ^& "\ChemMonitor USB.lnk" >> "%TEMP%\create_shortcut.vbs"
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> "%TEMP%\create_shortcut.vbs"
echo oLink.TargetPath = "wscript.exe" >> "%TEMP%\create_shortcut.vbs"
echo oLink.Arguments = """%~dp0chemmonitor_launcher.vbs""" >> "%TEMP%\create_shortcut.vbs"
echo oLink.WorkingDirectory = "%~dp0" >> "%TEMP%\create_shortcut.vbs"
echo oLink.Description = "ChemMonitor USB Display" >> "%TEMP%\create_shortcut.vbs"
echo oLink.Save >> "%TEMP%\create_shortcut.vbs"
cscript //nologo "%TEMP%\create_shortcut.vbs"
del "%TEMP%\create_shortcut.vbs"

echo [OK] Servicio instalado en Inicio de Windows
echo.
echo     Se ejecutara automaticamente al encender el PC.
echo     Para desinstalar, borra el acceso directo de:
echo     %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ChemMonitor USB.lnk
echo.

:: Also start it now
echo Iniciando ChemMonitor USB ahora...
start "" wscript.exe "%~dp0chemmonitor_launcher.vbs"
echo [OK] ChemMonitor USB ejecutandose en segundo plano
echo.
echo     Para detenerlo: taskkill /F /IM python.exe
echo.
pause
