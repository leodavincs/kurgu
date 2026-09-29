@echo off
rem Kurgu launcher for a git checkout on Windows: kurgu.cmd [project_dir] [--port N] [--no-open]
rem Installed with pipx/uv, use the `kurgu` command instead.
where py >nul 2>nul && (py -3 "%~dp0server.py" %* & exit /b %errorlevel%)
python "%~dp0server.py" %*
