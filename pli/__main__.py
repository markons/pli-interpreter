"""Command-line entry point:  python -m pli program.pli"""
import sys

from .interpreter import run_file, PLIError
from .parser import ParseError
from .lexer import LexError


def main(argv):
    if len(argv) != 1:
        print("usage: python -m pli <program.pli>", file=sys.stderr)
        return 2
    try:
        # Windows pipes often deliver UTF-8 with a BOM while Python decodes
        # stdin with the legacy console code page; utf-8-sig handles both
        # plain ASCII and BOM-prefixed UTF-8.
        sys.stdin.reconfigure(encoding="utf-8-sig")
    except (AttributeError, OSError, ValueError):
        pass
    try:
        run_file(argv[0])
    except (PLIError, ParseError, LexError) as e:
        print("PL/I error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
