@echo off
rem Kurgu launcher for a git checkout on Windows: kurgu.cmd [project_dir] [--port N] [--no-open]   |   kurgu.cmd notes list|wait|done <dir> ...
rem Installed with pipx/uv, use the `kurgu` command instead.
rem The exit code is passed on at the END of the script (outside any parenthesised block: %errorlevel% inside a block is expanded when the block is
rem parsed, which made every run exit 0 and hid `notes wait` timeouts).
setlocal
set "PYEXE=python"
rem a checkout runs with the interpreter that has its dependencies (an activated venv, the one that ran `pip install -r requirements.txt`): try `python` first
where python >nul 2>nul && python -c "import sys, numpy, PIL, fontTools; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && goto havepy
where py >nul 2>nul
if not errorlevel 1 set "PYEXE=py -3"
%PYEXE% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 goto havepy
rem not on PATH: a Python that uv manages (the installer puts uv in %USERPROFILE%\.local\bin)
set "UVPY="
for /f "usebackq delims=" %%P in (`uv python find ">=3.10" 2^>nul`) do set "UVPY=%%P"
if not defined UVPY if exist "%USERPROFILE%\.local\bin\uv.exe" for /f "usebackq delims=" %%P in (`"%USERPROFILE%\.local\bin\uv.exe" python find ">=3.10" 2^>nul`) do set "UVPY=%%P"
if not defined UVPY goto nopython
set PYEXE="%UVPY%"
:havepy
if /i "%~1"=="doctor" goto doctor
if /i "%~1"=="setup-ffmpeg" goto setupff
if /i "%~1"=="setup-agent" goto setupagent
if /i "%~1"=="wake" goto wake
if /i "%~1"=="notes" goto notes
%PYEXE% "%~dp0server.py" %*
exit /b %errorlevel%
:doctor
set "NARGS="
shift
:dloop
if "%~1"=="" goto drun
set NARGS=%NARGS% "%~1"
shift
goto dloop
:drun
%PYEXE% "%~dp0doctor.py" %NARGS%
exit /b %errorlevel%
:wake
set "NARGS="
shift
:wloop
if "%~1"=="" goto wrun
set NARGS=%NARGS% "%~1"
shift
goto wloop
:wrun
%PYEXE% "%~dp0kurgu_cli.py" wake %NARGS%
exit /b %errorlevel%
:setupagent
set "NARGS="
shift
:aloop
if "%~1"=="" goto arun
set NARGS=%NARGS% "%~1"
shift
goto aloop
:arun
%PYEXE% "%~dp0kurgu_cli.py" setup-agent %NARGS%
exit /b %errorlevel%
:setupff
set "NARGS="
shift
:sloop
if "%~1"=="" goto srun
set NARGS=%NARGS% "%~1"
shift
goto sloop
:srun
%PYEXE% "%~dp0ffmpeg_fetch.py" %NARGS%
exit /b %errorlevel%
:notes
rem rebuild the argument list without the word "notes": %* ignores SHIFT, and %1..%9 would cut long `done <dir> <ids> --reply "x"` lines
set "NARGS="
shift
:nloop
if "%~1"=="" goto nrun
set NARGS=%NARGS% "%~1"
shift
goto nloop
:nrun
%PYEXE% "%~dp0agent_notes.py" %NARGS%
exit /b %errorlevel%
:nopython
echo Kurgu needs Python 3.10 or newer. Easiest fix: run the installer ^(see README, "Install"^), it brings its own Python. 1>&2
exit /b 1
