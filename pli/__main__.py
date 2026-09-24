"""Command-line entry point:  python -m pli main.pli [sub.pli ...] [--parm STRING]

Several files are treated as separately compiled external procedures
linked into one program (the one with OPTIONS(MAIN) is the entry).

--parm STRING supplies the runtime parameter for a main procedure
declared  <label> : PROC(parmvar) OPTIONS(MAIN);  -- the mainframe
JCL PARM= convention; parmvar receives STRING as a CHAR VARYING value.
"""
import os
import sys

from .interpreter import run_files, PLIError
from .parser import ParseError
from .lexer import LexError


def main(argv):
    argv = list(argv)
    parm = ""
    if "--parm" in argv:
        i = argv.index("--parm")
        if i + 1 >= len(argv):
            print("--parm requires a value", file=sys.stderr)
            return 2
        parm = argv[i + 1]
        del argv[i:i + 2]
    if len(argv) < 1:
        prog = os.path.basename(sys.argv[0]) or "pli"
        if prog in ("__main__.py", "-c"):
            prog = "python -m pli"
        print("usage: %s <program.pli> [more.pli ...] [--parm STRING]" % prog,
              file=sys.stderr)
        return 2
    try:
        # Windows pipes often deliver UTF-8 with a BOM while Python decodes
        # stdin with the legacy console code page; utf-8-sig handles both
        # plain ASCII and BOM-prefixed UTF-8.
        sys.stdin.reconfigure(encoding="utf-8-sig")
    except (AttributeError, OSError, ValueError):
        pass
    try:
        run_files(argv, parm=parm)
    except (PLIError, ParseError, LexError) as e:
        print("PL/I error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
