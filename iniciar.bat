@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM  Sistema de turnos - arranque local
REM
REM  Doble click y listo. La primera vez tarda unos minutos porque
REM  crea el entorno virtual e instala las dependencias.
REM
REM  Puerto opcional:  iniciar.bat 8080
REM  Para cortarlo: Ctrl+C o cerrar la ventana.
REM ============================================================

cd /d "%~dp0"

set "PORT=%~1"
if "%PORT%"=="" set "PORT=8010"

set "VENV_PY=.venv\Scripts\python.exe"

echo.
echo ============================================================
echo   Sistema de turnos - entorno local
echo ============================================================
echo.

REM ---------- 1. Verificar que estamos en la carpeta correcta ----------
if not exist "app\main.py" goto :sin_proyecto

REM ---------- 2. Buscar Python ----------
set "PY_CMD="
py -3 --version >nul 2>&1 && set "PY_CMD=py -3"
if not defined PY_CMD (
    python --version >nul 2>&1 && set "PY_CMD=python"
)
if not defined PY_CMD goto :sin_python

REM ---------- 3. Entorno virtual ----------
if exist "%VENV_PY%" goto :venv_ok

echo [1/4] Creando el entorno virtual ^(solo la primera vez^)...
%PY_CMD% -m venv .venv
if errorlevel 1 goto :error_venv
echo       Entorno creado.
echo.
goto :instalar

:venv_ok
echo [1/4] Entorno virtual: OK

REM ---------- 4. Dependencias ----------
"%VENV_PY%" -c "import fastapi, uvicorn, sqlalchemy" >nul 2>&1
if not errorlevel 1 goto :deps_ok

:instalar
echo [2/4] Instalando dependencias... ^(puede tardar unos minutos^)
"%VENV_PY%" -m pip install --upgrade pip --quiet
"%VENV_PY%" -m pip install -r requirements.txt --quiet
if errorlevel 1 goto :error_deps
echo       Dependencias instaladas.
goto :config

:deps_ok
echo [2/4] Dependencias: OK

REM ---------- 5. Configuracion ----------
:config
if exist ".env" goto :env_ok

echo [3/4] No habia archivo .env: se copia desde .env.example
copy /y ".env.example" ".env" >nul
echo.
echo       IMPORTANTE: abri el archivo .env y completa ADMIN_EMAILS
echo       con tu email para poder entrar al panel de administracion.
echo.
goto :base

:env_ok
echo [3/4] Configuracion .env: OK

REM ---------- 6. Base de datos ----------
:base
if exist "turnos.db" goto :db_ok

echo [4/4] Creando la base con datos de ejemplo...
"%VENV_PY%" seed.py
if errorlevel 1 goto :error_seed
goto :arrancar

:db_ok
echo [4/4] Base de datos: OK

REM ---------- 7. Arrancar ----------
:arrancar
echo.
echo ============================================================
echo   Todo listo
echo ------------------------------------------------------------
echo   App        : http://localhost:%PORT%
echo   Panel      : http://localhost:%PORT%/admin
echo   API docs   : http://localhost:%PORT%/api/docs
echo.
echo   Para entrar sin Google usa el acceso de desarrollo
echo   que aparece en la pantalla de login.
echo.
echo   Los emails NO se envian: quedan como archivos .html
echo   en la carpeta outbox\ para que los revises.
echo ------------------------------------------------------------
echo   Ctrl+C o cerrar la ventana para detener el servidor.
echo ============================================================
echo.

REM Abre el navegador cuando el servidor ya esta escuchando
start "" /b cmd /c "timeout /t 4 /nobreak >nul & start "" http://localhost:%PORT%"

REM Sin --reload a proposito: con recarga automatica uvicorn levanta un proceso
REM hijo que sobrevive si cerras la ventana con la X y te deja el puerto ocupado.
REM Si estas editando codigo y lo queres, agregale --reload a esta linea.
"%VENV_PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%

echo.
echo El servidor se detuvo.
echo Si el puerto %PORT% estaba ocupado, proba con otro:  iniciar.bat 8080
echo.
pause
exit /b 0

REM ============================================================
REM  Errores
REM ============================================================
:sin_proyecto
echo [ERROR] No encuentro app\main.py en esta carpeta:
echo         %CD%
echo         Este archivo tiene que estar dentro de TURNOS-APP.
echo.
pause
exit /b 1

:sin_python
echo [ERROR] No encontre Python instalado.
echo         Descargalo de https://www.python.org/downloads/
echo         y tilda la opcion "Add Python to PATH" al instalar.
echo.
pause
exit /b 1

:error_venv
echo.
echo [ERROR] No se pudo crear el entorno virtual.
echo         Proba borrando la carpeta .venv y volviendo a ejecutar.
echo.
pause
exit /b 1

:error_deps
echo.
echo [ERROR] Fallo la instalacion de dependencias.
echo         Revisa tu conexion a internet y volve a ejecutar.
echo.
pause
exit /b 1

:error_seed
echo.
echo [ERROR] No se pudo crear la base de datos.
echo.
pause
exit /b 1
