@echo off
rem pli-build launcher: pli-build prog.pli -o prog
rem Works from any directory; %~dp0 is the folder containing this .bat,
rem which is also the folder containing the pli\ package.
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
python -m pli.build %*
endlocal
