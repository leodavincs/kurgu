@echo off
rem Kurgu launcher for a git checkout on Windows: kurgu.cmd [project_dir] [--port N] [--no-open]   |   kurgu.cmd notes list|wait|done <dir> ...
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
for %%A in (%*) do if /i "%%~A"=="--help" goto helpfor
for %%A in (%*) do if /i "%%~A"=="-h" goto helpfor
if /i "%~1"=="--help" goto help
if /i "%~1"=="-h" goto help
if /i "%~1"=="help" goto helptopic
if /i "%~1"=="doctor" goto doctor
if /i "%~1"=="setup-ffmpeg" goto setupff
if /i "%~1"=="setup-agent" goto setupagent
if /i "%~1"=="wake" goto wake
if /i "%~1"=="notes" goto notes
if /i "%~1"=="elements" goto elements
if /i "%~1"=="import" goto importer
if /i "%~1"=="rerender" goto rerender
if /i "%~1"=="frame" goto frame
if /i "%~1"=="strip" goto strip
if /i "%~1"=="probe" goto probe
if /i "%~1"=="report" goto report
if /i "%~1"=="detect" goto detect
if /i "%~1"=="ocr" goto detect
%PYEXE% "%~dp0server.py" %*
exit /b %errorlevel%
:helptopic
set "NARGS="
shift
:htloop
if "%~1"=="" goto htrun
set NARGS=%NARGS% "%~1"
shift
goto htloop
:htrun
%PYEXE% "%~dp0kurgu_cli.py" help %NARGS%
exit /b %errorlevel%
:helpfor
%PYEXE% "%~dp0kurgu_cli.py" "%~1" --help
exit /b %errorlevel%
:help
%PYEXE% "%~dp0kurgu_cli.py" --help
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
:frame
set "NARGS="
shift
:floop
if "%~1"=="" goto frun
set NARGS=%NARGS% "%~1"
shift
goto floop
:frun
%PYEXE% "%~dp0kurgu_cli.py" frame %NARGS%
exit /b %errorlevel%
:strip
set "NARGS="
shift
:striploop
if "%~1"=="" goto striprun
set NARGS=%NARGS% "%~1"
shift
goto striploop
:striprun
%PYEXE% "%~dp0kurgu_cli.py" strip %NARGS%
exit /b %errorlevel%
:elements
set "NARGS="
shift
:elementsloop
if "%~1"=="" goto elementsrun
set NARGS=%NARGS% "%~1"
shift
goto elementsloop
:elementsrun
%PYEXE% "%~dp0kurgu_cli.py" elements %NARGS%
exit /b %errorlevel%
:rerender
set "NARGS="
shift
:rerenderloop
if "%~1"=="" goto rerenderrun
set NARGS=%NARGS% "%~1"
shift
goto rerenderloop
:rerenderrun
%PYEXE% "%~dp0kurgu_cli.py" rerender %NARGS%
exit /b %errorlevel%
:importer
set "NARGS="
shift
:importerloop
if "%~1"=="" goto importerrun
set NARGS=%NARGS% "%~1"
shift
goto importerloop
:importerrun
%PYEXE% "%~dp0kurgu_cli.py" import %NARGS%
exit /b %errorlevel%
:probe
set "NARGS="
shift
:probeloop
if "%~1"=="" goto proberun
set NARGS=%NARGS% "%~1"
shift
goto probeloop
:proberun
%PYEXE% "%~dp0kurgu_cli.py" probe %NARGS%
exit /b %errorlevel%
:report
set "NARGS="
shift
:reportloop
if "%~1"=="" goto reportrun
set NARGS=%NARGS% "%~1"
shift
goto reportloop
:reportrun
%PYEXE% "%~dp0kurgu_cli.py" report %NARGS%
exit /b %errorlevel%
:detect
set "NARGS="
shift
:detloop
if "%~1"=="" goto detrun
set NARGS=%NARGS% "%~1"
shift
goto detloop
:detrun
%PYEXE% "%~dp0kurgu_cli.py" detect %NARGS%
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
