"""Command-line entry point:  python -m pli main.pli [sub.pli ...]

Several files are treated as separately compiled external procedures
linked into one program (the one with OPTIONS(MAIN) is the entry).
"""
import sys

from .interpreter import run_files, PLIError
from .parser import ParseError
from .lexer import LexError


def main(argv):
    if len(argv) < 1:
        print("usage: python -m pli <program.pli> [more.pli ...]",
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
        run_files(argv)
    except (PLIError, ParseError, LexError) as e:
        print("PL/I error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
