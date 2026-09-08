"""Scripted test for the %INCLUDE DB2 source-repository fallback.

Run from the repo root: python pli/examples/include_fetch_demo/test_include_fetch.py

1. Builds the sqlite fixture (SOURCE_REPO table with one REMOTEMEM row).
2. Runs include_fetch_demo.pli -- the member isn't a local file, so it
   must be fetched from the table and cached to .pli_include_cache/.
3. Empties SOURCE_REPO and re-runs the program -- it must still work,
   proving the cache-hit path never touches the DB again.
4. Cleans up the generated sqlite file and cache directory.
"""
import os
import subprocess
import sys
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DEMO_PLI = os.path.join(HERE, "include_fetch_demo.pli")
DB_PATH = os.path.join(HERE, "include_fetch_demo.sqlite")
CACHE_DIR = os.path.join(HERE, ".pli_include_cache")
CACHE_FILE = os.path.join(CACHE_DIR, "REMOTEMEM.pli")

EXPECTED = ("before include\n"
           "inside REMOTEMEM\n"
           "FETCHED =               42\n"
           "after include\n")


def run_demo():
    result = subprocess.run(
        [sys.executable, "-m", "pli", DEMO_PLI],
        cwd=ROOT, capture_output=True, text=True)
    return result


def main():
    sys.path.insert(0, HERE)
    import build_fixture
    build_fixture.build()

    try:
        result = run_demo()
        assert result.returncode == 0, (
            "first run failed: %s" % result.stderr)
        assert result.stdout == EXPECTED, (
            "first run output mismatch:\n%r\nexpected:\n%r"
            % (result.stdout, EXPECTED))
        assert os.path.exists(CACHE_FILE), (
            "cache file was not created: %s" % CACHE_FILE)
        print("PASS: first run fetched from DB and cached correctly")

        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM SOURCE_REPO")
        conn.commit()
        conn.close()

        result = run_demo()
        assert result.returncode == 0, (
            "second run (cache-only) failed: %s" % result.stderr)
        assert result.stdout == EXPECTED, (
            "second run output mismatch:\n%r\nexpected:\n%r"
            % (result.stdout, EXPECTED))
        print("PASS: second run used the cache, DB was empty")
    finally:
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
        if os.path.isdir(CACHE_DIR):
            os.rmdir(CACHE_DIR)

    print("include_fetch_demo self-test passed")


if __name__ == "__main__":
    main()
