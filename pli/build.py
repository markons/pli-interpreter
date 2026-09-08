"""pli-build: freeze a .pli program (or the interpreter itself) into a
single-file executable via PyInstaller.

    pli-build prog.pli [sub.pli ...] -o prog     # bakes the program in;
                                                  # ./prog needs no args
    pli-build --interpreter -o pli               # general-purpose frozen
                                                  # interpreter; ./pli x.pli

Both modes need no speedup over `python -m pli` (same tree-walking
interpreter, just bundled with its own Python runtime) -- the point is
running on a target with no Python install. Startup is dominated by
PyInstaller's onefile self-extraction (roughly 1s); output is roughly
15-30MB.

pli/sql.py imports jaydebeapi/jpype/ibm_db_dbi lazily inside functions
(only loaded if a program actually runs EXEC SQL against Db2), but
PyInstaller's static bytecode scanner bundles every module it finds an
`import` for regardless of where the import lives -- so by default
this tool explicitly excludes those modules (sqlite still works; pass
--with-db2 for programs that need Db2), which is what keeps ordinary
non-SQL builds fast and small.
"""
import argparse
import os
import shutil
import sys
import tempfile

from .parser import PLIParser, ParseError
from .lexer import LexError

CLIDRIVER_REL_PATH = os.path.join("clidriver", "bin")

# Repo root is one level up from this file (pli/build.py -> pli-interpreter/).
# Generated PyInstaller entry scripts are kept here (not in a temp dir that
# gets wiped) so they can be inspected after the build.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHONCODE_DIR = os.path.join(_ROOT, "pythoncode")

_PROGRAM_ENTRY = '''\
import os
import sys

def _resolve(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.join(base, name)

def main():
    from pli.interpreter import run_files, PLIError
    from pli.parser import ParseError
    from pli.lexer import LexError
    try:
        sys.stdin.reconfigure(encoding="utf-8-sig")
    except (AttributeError, OSError, ValueError):
        pass
    sources = {sources!r}
    try:
        run_files([_resolve(name) for name in sources])
    except (PLIError, ParseError, LexError) as e:
        print("PL/I error: %s" % e, file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
'''

_INTERPRETER_ENTRY = '''\
import sys

def main():
    from pli.__main__ import main as pli_main
    sys.exit(pli_main(sys.argv[1:]))

if __name__ == "__main__":
    main()
'''


def _validate_sources(paths):
    """Parse-check every source file up front so a syntax error is
    reported immediately instead of after a multi-second PyInstaller run."""
    parser = PLIParser()
    parser.build(write_tables=False)
    for path in paths:
        with open(path, "r", encoding="utf-8-sig") as f:
            parser.parse(f.read())


# Only needed for Db2 (ibm_db native driver, or the Kerberos+SSL JDBC
# path via jaydebeapi/jpype). Excluded by default -- PyInstaller's
# static scanner would otherwise bundle the whole JVM-bridge stack for
# programs that never touch EXEC SQL, ballooning build time and size.
_DB2_ONLY_MODULES = [
    "ibm_db", "ibm_db_dbi", "jaydebeapi", "jpype", "jpype1",
]


def _ibm_db_data_args():
    """Best-effort: bundle ibm_db's clidriver tree if ibm_db is installed
    in the build environment. Returns a list of --add-data args, or []."""
    try:
        import ibm_db
    except ImportError:
        print("pli-build: ibm_db not installed in this environment -- "
              "building without Db2 support (sqlite driver still works)",
              file=sys.stderr)
        return []
    clidriver_dir = os.path.join(os.path.dirname(ibm_db.__file__),
                                 "clidriver", "bin")
    if not os.path.isdir(clidriver_dir):
        print("pli-build: ibm_db installed but its clidriver/bin tree "
              "was not found -- building without it", file=sys.stderr)
        return []
    return ["--add-data", "%s%s%s" % (clidriver_dir, os.pathsep,
                                      CLIDRIVER_REL_PATH)]


def build(sources, output, clean=False, verbose=False, interpreter=False,
          with_db2=False):
    from PyInstaller.__main__ import run as pyinstaller_run

    if not interpreter:
        _validate_sources(sources)

    out_dir, out_name = os.path.split(output)
    out_dir = os.path.abspath(out_dir) if out_dir else os.getcwd()

    work = tempfile.mkdtemp(prefix="pli-build-")
    try:
        entry_dir = os.path.join(PYTHONCODE_DIR, out_name)
        os.makedirs(entry_dir, exist_ok=True)
        entry_path = os.path.join(entry_dir, "_pli_build_entry.py")
        with open(entry_path, "w", encoding="utf-8") as f:
            if interpreter:
                f.write(_INTERPRETER_ENTRY)
            else:
                f.write(_PROGRAM_ENTRY.format(
                    sources=[os.path.basename(p) for p in sources]))

        dist_dir = os.path.join(work, "dist")
        args = [
            entry_path,
            "--onefile",
            "--name", out_name,
            "--distpath", dist_dir,
            "--workpath", os.path.join(work, "build"),
            "--specpath", work,
            "--hidden-import", "ply.lex",
            "--hidden-import", "ply.yacc",
        ]
        if not interpreter:
            for src in sources:
                args += ["--add-data", "%s%s." % (os.path.abspath(src),
                                                   os.pathsep)]
        if with_db2:
            args += _ibm_db_data_args()
        else:
            for mod in _DB2_ONLY_MODULES:
                args += ["--exclude-module", mod]
        if clean:
            args.append("--clean")
        if not verbose:
            args += ["--log-level", "WARN"]

        pyinstaller_run(args)

        built_name = out_name + (".exe" if os.name == "nt" else "")
        built_path = os.path.join(dist_dir, built_name)
        os.makedirs(out_dir, exist_ok=True)
        final_path = os.path.join(out_dir, built_name)
        shutil.copy2(built_path, final_path)
        print("pli-build: wrote %s" % final_path)
        return final_path
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pli-build")
    ap.add_argument("sources", nargs="*", metavar="prog.pli",
                    help="PL/I source file(s) to bake into the binary")
    ap.add_argument("-o", "--output", required=True,
                    help="output binary name (no extension)")
    ap.add_argument("--interpreter", action="store_true",
                    help="freeze the general-purpose interpreter instead "
                         "of baking in specific source files")
    ap.add_argument("--with-db2", action="store_true",
                    help="bundle ibm_db/jaydebeapi for EXEC SQL against "
                         "Db2 (bigger, slower build; omit for sqlite-only "
                         "or non-SQL programs)")
    ap.add_argument("--clean", action="store_true",
                    help="wipe PyInstaller's cache before building")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    if args.interpreter and args.sources:
        ap.error("--interpreter takes no source files")
    if not args.interpreter and not args.sources:
        ap.error("at least one source file is required (or --interpreter)")

    try:
        build(args.sources, args.output, clean=args.clean,
              verbose=args.verbose, interpreter=args.interpreter,
              with_db2=args.with_db2)
    except (ParseError, LexError) as e:
        print("PL/I error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
