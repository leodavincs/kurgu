@echo off
rem Kurgu launcher for a git checkout on Windows: kurgu.cmd [project_dir] [--port N] [--no-open]   |   kurgu.cmd <command> ...   (every command: kurgu.cmd --help)
rem Installed with pipx/uv, use the `kurgu` command instead.
rem The exit code is passed on at the END of the script (outside any parenthesised block: %errorlevel% inside a block is expanded when the block is
rem parsed, which made every run exit 0 and hid `notes wait` timeouts).
setlocal
rem never run a program from the current folder (a downloaded project could hold python.bat / uv.exe): cmd skips the cwd for bare commands with this set, and `where $PATH:x` looks in PATH only
set "NoDefaultCurrentDirectoryInExePath=1"
set "PYEXE=python"
rem a checkout runs with the interpreter that has its dependencies (an activated venv, the one that ran `pip install -r requirements.txt`): try `python` first
where $PATH:python >nul 2>nul && python -c "import sys, numpy, PIL, fontTools; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && goto havepy
where $PATH:py >nul 2>nul
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
rem Every command goes through kurgu_cli.py with the arguments exactly as typed (%*). No SHIFT here: SHIFT also moves %0, so %~dp0 after it
rem pointed at the current folder, and cmd splits %1..%9 at commas, so `notes add --rect 100,200,300,120` arrived as four arguments.
rem kurgu_cli.py routes `notes`, `wake`, `doctor`, `--help` anywhere, ... the same way the installed `kurgu` command does.
%PYEXE% "%~dp0kurgu_cli.py" %*
exit /b %errorlevel%
:nopython
echo Kurgu needs Python 3.10 or newer. Easiest fix: run the installer ^(see README, "Install"^), it brings its own Python. 1>&2
exit /b 1
