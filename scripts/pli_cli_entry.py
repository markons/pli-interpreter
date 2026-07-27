"""PyInstaller entry point for the standalone pli CLI executable.

Not meant to be run directly in normal development -- use
`python -m pli program.pli` for that.  This file exists only so
PyInstaller has a plain script (not a package `-m` invocation) to
analyze and freeze; it imports `pli` as a regular package so every
relative import inside the package resolves normally.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                 "..")))

from pli.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
