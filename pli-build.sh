#!/bin/sh
# pli-build launcher (Unix): ./pli-build.sh prog.pli -o prog
# Works from any directory; the pli package lives alongside this script.
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 || command -v python)"
PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}" exec "$PY" -m pli.build "$@"
