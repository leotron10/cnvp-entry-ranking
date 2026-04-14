@echo off
cd /d "%~dp0"

:: Comprobar que Python esta instalado
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no encontrado. Ejecuta primero "setup.bat".
    pause
    exit /b 1
)

:: Comprobar que Streamlit esta instalado
python -m streamlit --version >nul 2>&1
if errorlevel 1 (
    echo Streamlit no instalado. Ejecutando instalacion...
    python -m pip install -r requirements.txt
)

:: Lanzar la aplicacion
start "" http://localhost:8501
python -m streamlit run app.py --server.headless true
