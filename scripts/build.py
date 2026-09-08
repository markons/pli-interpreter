"""Build standalone pli executables for the current platform.

Two modes:

    python scripts/build.py
        Builds the generic interpreter: dist/pli(.exe), which runs
        ANY program you pass it afterwards (`pli program.pli`), plus
        a packaged dist/pli-<version>-<os>-<arch>[.zip] with the
        examples and docs bundled alongside it.

    python scripts/build.py program.pli [more.pli ...] [-o name]
        Compiles a SPECIFIC PL/I program (or a separately-compiled
        multi-file program) into its own standalone executable that
        needs no arguments to run: dist/<name>(.exe).  The % compile-
        time preprocessor (%INCLUDE, %DO, %PROC, ...) is expanded once
        at build time and the resulting source is embedded directly in
        the executable, so the built program has no dependency on the
        original .pli file(s) or their directory at run time.  Default
        output name is the first source file's basename.

Runs on Windows and Linux (and should work unmodified on macOS) -- it
inspects the platform it is running ON and produces a native binary
for THAT platform via PyInstaller.  There is no cross-compilation: to
get both a Windows and a Linux binary, run this script once on each OS
(that is what the GitHub Actions release matrix in
.github/workflows/release.yml does automatically for interpreter-mode
builds).

The build intentionally does NOT bundle the optional ibm_db (Db2)
driver: it pulls in a large native client library with its own
redistribution terms, so Db2 support (and hence EXEC SQL CONNECT TO a
Db2 database) stays a source-install extra (`pip install ibm_db`) not
available in binaries built by this script.  sqlite-backed EXEC SQL
works out of the box, since sqlite3 is part of the Python standard
library.
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ENTRY = os.path.join(ROOT, "scripts", "pli_cli_entry.py")
DIST = os.path.join(ROOT, "dist")
BUILD = os.path.join(ROOT, "build", "pyinstaller")
# Generated PyInstaller entry scripts are kept here (not under build/, which
# --clean wipes) so they can be inspected after the build.
PYTHONCODE_DIR = os.path.join(ROOT, "pythoncode")

sys.path.insert(0, ROOT)


def _exe_suffix():
    return ".exe" if platform.system() == "Windows" else ""


def _platform_tag():
    system = platform.system()
    tag = {"Windows": "windows", "Linux": "linux",
          "Darwin": "macos"}.get(system, system.lower())
    machine = platform.machine().lower()
    arch = "x64" if machine in ("amd64", "x86_64") else \
        "arm64" if machine in ("arm64", "aarch64") else machine
    return tag, arch


def _version():
    import pli
    return pli.__version__


def check_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Run:\n"
             "    pip install pyinstaller\nand try again.",
             file=sys.stderr)
        sys.exit(2)


def run_pyinstaller(entry, name):
    import PyInstaller.__main__
    args = [
        entry,
        "--name", name,
        "--onefile",
        "--distpath", DIST,
        "--workpath", os.path.join(BUILD, name),
        "--specpath", os.path.join(BUILD, name),
        "--console",
        "--clean",
        "--noconfirm",
        # ply is imported dynamically enough that an explicit hint helps
        "--hidden-import", "ply.lex",
        "--hidden-import", "ply.yacc",
    ]
    print("Running PyInstaller:\n  " + " ".join(args))
    PyInstaller.__main__.run(args)
    exe = os.path.join(DIST, name + _exe_suffix())
    if not os.path.exists(exe):
        print("build failed: %s not found" % exe, file=sys.stderr)
        sys.exit(1)
    return exe


# ---- mode 1: the generic, reusable interpreter --------------------------

def package_interpreter(exe_path, version, tag, arch):
    folder_name = "pli-%s-%s-%s" % (version, tag, arch)
    folder = os.path.join(DIST, folder_name)
    if os.path.exists(folder):
        shutil.rmtree(folder)
    os.makedirs(folder)

    shutil.copy2(exe_path, folder)
    shutil.copytree(os.path.join(ROOT, "pli", "examples"),
                    os.path.join(folder, "examples"))
    for doc in ("README.md", "syntax.md"):
        src = os.path.join(ROOT, doc)
        if os.path.exists(src):
            shutil.copy2(src, folder)

    zip_path = folder + ".zip"
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _, filenames in os.walk(folder):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                arcname = os.path.join(folder_name,
                                       os.path.relpath(full, folder))
                zf.write(full, arcname)
    return folder, zip_path


def build_interpreter():
    check_pyinstaller()
    version = _version()
    tag, arch = _platform_tag()
    print("Building the pli interpreter %s for %s-%s ..."
         % (version, tag, arch))
    exe_path = run_pyinstaller(ENTRY, "pli")
    folder, zip_path = package_interpreter(exe_path, version, tag, arch)
    print("\nBuilt:")
    print("  " + folder)
    print("  " + zip_path)

    exe = os.path.join(folder, "pli" + _exe_suffix())
    hello = os.path.join(folder, "examples", "hello.pli")
    print("\nSmoke test: %s %s" % (exe, hello))
    result = subprocess.run([exe, hello], input="World\n",
                            capture_output=True, text=True)
    print(result.stdout, end="")
    if result.returncode != 0 or "AND HELLO," not in result.stdout:
        print("SMOKE TEST FAILED (exit %d)" % result.returncode,
             file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    print("Smoke test passed.")


# ---- mode 2: compile one specific program into its own executable ------

_PROGRAM_ENTRY_HEADER = '''"""Auto-generated by scripts/build.py -- a standalone compiled PL/I
program.  Do not edit; regenerate by re-running the build.
"""
import sys

sys.path.insert(0, {root!r})

from pli.interpreter import Interpreter, PLIError
from pli.parser import ParseError
from pli.lexer import LexError

# (source, label) pairs, one per compiled unit; the % preprocessor was
# already expanded at build time, so this is the final source text.
SOURCES = '''

_PROGRAM_ENTRY_FOOTER = '''

def main():
    try:
        sys.stdin.reconfigure(encoding="utf-8-sig")
    except (AttributeError, OSError, ValueError):
        pass
    interp = Interpreter()
    try:
        interp.run_multi(SOURCES)
    except (PLIError, ParseError, LexError) as e:
        print("PL/I error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def _generate_program_entry(paths, out_dir):
    from pli.interpreter import _strip_shebang
    from pli.preproc import preprocess

    sources = []
    for path in paths:
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
        text = _strip_shebang(text)
        incdir = os.path.dirname(os.path.abspath(path))
        expanded = preprocess(text, incdir)
        sources.append((expanded, "<embedded: %s>" % os.path.basename(path)))

    os.makedirs(out_dir, exist_ok=True)
    entry_path = os.path.join(out_dir, "_program_entry.py")
    with open(entry_path, "w", encoding="utf-8") as f:
        f.write(_PROGRAM_ENTRY_HEADER.format(root=ROOT))
        f.write(repr(sources))
        f.write(_PROGRAM_ENTRY_FOOTER)
    return entry_path


def build_program(paths, output_name):
    check_pyinstaller()
    for p in paths:
        if not os.path.exists(p):
            print("no such file: %s" % p, file=sys.stderr)
            sys.exit(2)
    name = output_name or os.path.splitext(os.path.basename(paths[0]))[0]
    print("Compiling %s -> %s%s ..."
         % (", ".join(paths), name, _exe_suffix()))

    entry_dir = os.path.join(PYTHONCODE_DIR, name)
    entry = _generate_program_entry(paths, entry_dir)
    exe_path = run_pyinstaller(entry, name)
    print("\nBuilt: " + exe_path)

    print("\nSmoke test (empty stdin, 15s timeout): %s" % exe_path)
    try:
        result = subprocess.run([exe_path], input="", capture_output=True,
                                text=True, timeout=15)
        print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        print("(exited %d -- a non-zero exit here just means the program "
             "itself wanted input or ended abnormally; it does not mean "
             "the BUILD failed)" % result.returncode)
    except subprocess.TimeoutExpired:
        print("(program did not finish within 15s with empty input --"
             " likely fine if it waits on SYSIN; run it yourself with"
             " real input to check)")


# ---- entry point -------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Build a standalone pli CLI executable, or compile "
                    "one specific PL/I program into its own executable.")
    ap.add_argument("sources", nargs="*",
                    help="PL/I source file(s) to compile into a "
                         "standalone, argument-free executable. Omit "
                         "to build the generic reusable interpreter "
                         "instead.")
    ap.add_argument("-o", "--output", default=None,
                    help="output executable name (default: the first "
                         "source file's basename)")
    args = ap.parse_args()

    if args.sources:
        build_program(args.sources, args.output)
    else:
        if args.output:
            ap.error("-o/--output only applies when compiling a program "
                     "(pass one or more .pli files)")
        build_interpreter()


if __name__ == "__main__":
    main()
