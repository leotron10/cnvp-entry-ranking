@echo off
cd /d "%~dp0"
echo =============================================
echo   CNVP Entry Ranking - Instalacion inicial
echo =============================================
echo.

:: Comprobar que Python esta instalado
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no esta instalado o no esta en el PATH.
    echo Descargalo desde https://www.python.org/downloads/
    echo Asegurate de marcar "Add Python to PATH" al instalarlo.
    pause
    exit /b 1
)

echo Python encontrado. Instalando dependencias...
echo.
python -m pip install -r requirements.txt

echo.
echo =============================================
echo   Instalacion completada!
echo   Ya puedes abrir el programa con:
echo   "CNVP Entry Ranking.bat"
echo =============================================
pause
