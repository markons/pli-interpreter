@echo off
rem Launch the PL/I IDE (optionally with a file): pli-ide [program.pli]
start "PL/I IDE" pythonw "%~dp0pli_ide.py" %*
