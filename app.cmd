@echo off
rem Windows launcher: equivalent to `python app` with the same exit code.
setlocal
python "%~dp0app" %*
exit /b %ERRORLEVEL%
