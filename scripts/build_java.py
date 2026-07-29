#!/usr/bin/env python3
"""Build a PL/I program into Java using pli/javagen.py (experimental).

    python scripts/build_java.py program.pli [-o outdir] [--run] [--diff]

Pipeline: preprocess -> parse (pli's own PLY grammar) -> pli.javagen ->
write <ClassName>.java + copy javart/PLI.java -> javac -> optionally
run it (--run), optionally diff its output against `python -m pli`
(--diff) for a quick differential-testing check.
"""
import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pli.preproc import preprocess, PreprocError      # noqa: E402
from pli.parser import PLIParser, ParseError          # noqa: E402
from pli.lexer import LexError                        # noqa: E402
from pli.javagen import generate, CodegenError         # noqa: E402


def class_name_for(path):
    base = os.path.splitext(os.path.basename(path))[0]
    safe = "".join(c if c.isalnum() else "_" for c in base)
    if not safe or safe[0].isdigit():
        safe = "P_" + safe
    return safe[0].upper() + safe[1:]


def build(pli_path, outdir):
    with open(pli_path, "r", encoding="utf-8") as f:
        source = f.read()
    incdir = os.path.dirname(os.path.abspath(pli_path))
    source = preprocess(source, incdir)
    ast = PLIParser().parse(source)
    class_name = class_name_for(pli_path)
    java_src = generate(ast, class_name)

    os.makedirs(outdir, exist_ok=True)
    java_path = os.path.join(outdir, class_name + ".java")
    with open(java_path, "w", encoding="utf-8") as f:
        f.write(java_src)
    rt_src = os.path.join(ROOT, "javart", "PLI.java")
    shutil.copy(rt_src, os.path.join(outdir, "PLI.java"))
    return class_name, java_path


def javac_compile(outdir):
    javac = shutil.which("javac")
    if not javac:
        print("javac not found on PATH; generated source left in %s"
             % outdir, file=sys.stderr)
        return False
    java_files = [os.path.join(outdir, f) for f in os.listdir(outdir)
                 if f.endswith(".java")]
    r = subprocess.run([javac, "-d", outdir, "-encoding", "UTF-8"] + java_files,
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout, file=sys.stderr)
        print(r.stderr, file=sys.stderr)
        return False
    return True


def java_run(outdir, class_name, stdin_text=None):
    java = shutil.which("java")
    r = subprocess.run([java, "-cp", outdir, class_name],
                       input=stdin_text, capture_output=True, text=True,
                       timeout=15)
    return r.stdout, r.stderr, r.returncode


def python_run(pli_path, stdin_text=None):
    r = subprocess.run([sys.executable, "-m", "pli", pli_path],
                       input=stdin_text, capture_output=True, text=True,
                       cwd=ROOT, timeout=15)
    return r.stdout, r.stderr, r.returncode


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("pli_file")
    ap.add_argument("-o", "--outdir", default=None)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--diff", action="store_true",
                    help="also run python -m pli and diff stdout")
    ap.add_argument("--stdin", default=None,
                    help="text to feed as SYSIN for --run/--diff")
    args = ap.parse_args(argv)

    outdir = args.outdir or os.path.join(
        ROOT, "build", "java", class_name_for(args.pli_file))
    try:
        class_name, java_path = build(args.pli_file, outdir)
    except (LexError, ParseError, PreprocError, CodegenError) as e:
        msgs = getattr(e, "messages", None) or [str(e)]
        for m in msgs:
            print("%s: %s" % (type(e).__name__, m), file=sys.stderr)
        return 1
    print("wrote %s" % java_path)

    if args.run or args.diff:
        if not javac_compile(outdir):
            return 1
        print("compiled OK -> %s" % outdir)
        j_out, j_err, j_rc = java_run(outdir, class_name, args.stdin)
        print("--- java stdout ---")
        print(j_out, end="")
        if j_err:
            print("--- java stderr ---", file=sys.stderr)
            print(j_err, end="", file=sys.stderr)

    if args.diff:
        p_out, p_err, p_rc = python_run(args.pli_file, args.stdin)
        print("--- python stdout ---")
        print(p_out, end="")
        # Python's stdout is opened in text mode, which on Windows
        # translates \n -> \r\n; Java's PrintStream never does. That's a
        # platform text-mode artifact, not a behavioral difference, so
        # normalize both sides before comparing.
        if j_out.replace("\r\n", "\n") == p_out.replace("\r\n", "\n"):
            print("*** MATCH: java and python -m pli produced identical "
                 "stdout ***")
        else:
            print("*** MISMATCH ***", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
