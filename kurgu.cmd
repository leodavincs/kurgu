@echo off
rem Kurgu launcher for a git checkout on Windows: kurgu.cmd [project_dir] [--port N] [--no-open]   |   kurgu.cmd notes list|wait|done <dir> ...
rem Installed with pipx/uv, use the `kurgu` command instead.
rem The exit code is passed on at the END of the script (outside any parenthesised block: %errorlevel% inside a block is expanded when the block is
rem parsed, which made every run exit 0 and hid `notes wait` timeouts).
setlocal
set "PYEXE=python"
where py >nul 2>nul
if not errorlevel 1 set "PYEXE=py -3"
%PYEXE% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto nopython
if /i "%~1"=="notes" goto notes
%PYEXE% "%~dp0server.py" %*
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
echo Kurgu needs Python 3.10 or newer ^(https://www.python.org/downloads/^) 1>&2
exit /b 1
