"""Scripted test for GENERIC, DO REPEAT and iSUB defining (stage11.pli).

Run standalone from the repo root:  python pli/examples/test_stage11.py
Checks stage11.pli's exact output plus the error paths (no GENERIC
match, REPEAT with TO, iSUB on a scalar, iSUB outside DEFINED,
iSUB beyond the defined array's rank, subscript range on a DEFINED
array).
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from pli.interpreter import Interpreter, PLIError, PLICondition  # noqa: E402
from pli.parser import ParseError                                # noqa: E402

EXPECTED = """\

SHOW_BIN                7
SHOW_DEC                12.50
SHOW_FLT                2.5
SHOW_CHR                HELLO
SHOW_TWO                7                       HELLO
SHOW_VEC                6
SHOW_ANY                '1010'B
SHOW_DEC                42
SHOW_BIN                8
BIGGER(3,9)   =         9
BIGGER(AB,AC) =         AC
POWERS OF 2 :    1    2    4    8   16   32   64
UNTIL       :    3    7   11   15   19
LIST        :   10   20   30
DIAG        :   11   22   33
ROW2        :   21   22   23
TR(1,3)/W(1,3):         31                      13
A AFTER     :
    0   12   13
   99    0   23
   31   32    0
"""


def run(src):
    out = io.StringIO()
    Interpreter(stdout=out).run_multi([(src, HERE)])
    return out.getvalue()


def expect_error(label, src, needle):
    try:
        run(src)
    except (PLIError, PLICondition, ParseError) as e:
        msg = "; ".join(getattr(e, "messages", None) or [str(e)])
        assert needle in msg, "%s: wrong error: %s" % (label, msg)
        print("ok   %-28s -> %s" % (label, msg.strip().splitlines()[0]))
        return
    raise AssertionError("%s: no error raised" % label)


def main():
    with open(os.path.join(HERE, "stage11.pli"), encoding="utf-8") as f:
        got = run(f.read())
    norm = lambda s: s.replace("\r\n", "\n").rstrip("\n")   # noqa: E731
    if norm(got) != norm(EXPECTED):
        print(got)
        raise AssertionError("stage11.pli output differs")
    print("ok   stage11.pli output")

    expect_error("generic: no match", """
T: PROC OPTIONS(MAIN);
   DCL G GENERIC(A WHEN(FIXED), B WHEN(FLOAT));
   DCL S CHAR(3) INIT('XYZ');
   CALL G(S);
A: PROC(X); DCL X FIXED; END A;
B: PROC(X); DCL X FLOAT; END B;
END T;""", "no member of GENERIC G")
    expect_error("generic: arg count", """
T: PROC OPTIONS(MAIN);
   DCL G GENERIC(A WHEN(FIXED));
   CALL G(1, 2);
A: PROC(X); DCL X FIXED; END A;
END T;""", "no member of GENERIC G")
    expect_error("generic: member not proc", """
T: PROC OPTIONS(MAIN);
   DCL G GENERIC(NOPE WHEN(*));
   CALL G(1);
END T;""", "is not a procedure")
    expect_error("generic: misspelt keyword", """
T: PROC OPTIONS(MAIN);
   DCL G GENERICX(A WHEN(FIXED));
END T;""", "only valid for GENERIC")
    expect_error("repeat with TO", """
T: PROC OPTIONS(MAIN);
   DO I = 1 TO 5 REPEAT I + 1; END;
END T;""", "REPEAT cannot be combined")
    expect_error("unknown DO option", """
T: PROC OPTIONS(MAIN);
   DO I = 1 REPEATX I + 1; END;
END T;""", "expected TO, BY, REPEAT")
    expect_error("iSUB on scalar", """
T: PROC OPTIONS(MAIN);
   DCL A(3) FIXED; DCL X FIXED DEFINED A(1SUB);
END T;""", "iSUB defining needs a dimensioned")
    expect_error("iSUB outside DEFINED", """
T: PROC OPTIONS(MAIN);
   DCL X FIXED; X = 1SUB;
END T;""", "only valid in the base reference")
    expect_error("iSUB beyond rank", """
T: PROC OPTIONS(MAIN);
   DCL A(3,3) FIXED; DCL D(3) FIXED DEFINED A(1SUB, 2SUB);
   PUT LIST(D(1));
END T;""", "exceeds the 1 dimension")
    expect_error("DEFINED subscriptrange", """
T: PROC OPTIONS(MAIN);
   DCL A(3,3) FIXED; DCL D(3) FIXED DEFINED A(1SUB, 1SUB);
   PUT LIST(D(4));
END T;""", "out of range")

    # REPEAT is still usable as the REPEAT(s,n) builtin
    out = run("""
T: PROC OPTIONS(MAIN);
   PUT LIST(REPEAT('AB', 2));
END T;""")
    assert "ABABAB" in out, out
    print("ok   REPEAT builtin unaffected")
    print("all stage11 tests passed")


if __name__ == "__main__":
    main()
