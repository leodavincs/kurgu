@echo off
rem Kurgu launcher for a git checkout on Windows: kurgu.cmd [project_dir] [--port N] [--no-open]   |   kurgu.cmd notes list|wait|done <dir> ...
rem Installed with pipx/uv, use the `kurgu` command instead.
rem The exit code is passed on at the END of the script (outside any parenthesised block: %errorlevel% inside a block is expanded when the block is
rem parsed, which made every run exit 0 and hid `notes wait` timeouts).
setlocal
set "PYEXE=python"
where py >nul 2>nul
if not errorlevel 1 set "PYEXE=py -3"
if /i "%~1"=="notes" goto notes
%PYEXE% "%~dp0server.py" %*
exit /b %errorlevel%
:notes
shift
%PYEXE% "%~dp0agent_notes.py" %1 %2 %3 %4 %5 %6 %7 %8 %9
exit /b %errorlevel%
