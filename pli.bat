@echo off
rem PL/I interpreter launcher: pli program.pli
rem Works from any directory; %~dp0 is the folder containing this .bat,
rem which is also the folder containing the pli\ package.
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
python -m pli %*
endlocal
