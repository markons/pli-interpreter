"""One-time setup for include_fetch_demo.pli's scripted test: creates
include_fetch_demo.sqlite with a SOURCE_REPO table (mirroring a DB2
CLOB-based source-repository table) and one REMOTEMEM row whose source
text uses the newline_char placeholder instead of real newlines.

Run from this directory: python build_fixture.py
"""
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "include_fetch_demo.sqlite")
NEWLINE_CHAR = "\x9f"

MEMBER_SOURCE = ("   PUT SKIP LIST('inside REMOTEMEM');" + NEWLINE_CHAR +
                "   DCL FETCHED FIXED INIT(42);" + NEWLINE_CHAR +
                "   PUT SKIP LIST('FETCHED =', FETCHED);")


def build():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE SOURCE_REPO "
                 "(CCMOBJT TEXT, CCMTYPT TEXT, QUELLE TEXT)")
    conn.execute("INSERT INTO SOURCE_REPO VALUES (?, ?, ?)",
                 ("REMOTEMEM", "INCLUDE", MEMBER_SOURCE))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    build()
    print("built", DB_PATH)
