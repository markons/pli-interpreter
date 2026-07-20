# CLAUDE.md — project notes for pli-interpreter

PL/I(F)-level interpreter in Python (PLY-based) + Tkinter IDE + embedded
SQL. Owner: markons (Gabor Markon). Repo: github.com/markons/pli-interpreter.

## THIS folder is the source of truth
Develop ONLY in this repo clone. A stale pre-repo copy may still exist at
`C:\Users\maga1\Documents\code\pli\` (+ `pli_ide.py` beside it) — it lacks
SQL support and newer fixes; do not edit it (deletion pending owner's OK).

## Run / test
- Run a program: `python -m pli pli\examples\hello.pli` (from repo root);
  Windows: `pli.bat`, Unix: `bin/pli` (`bin/` scripts are LF + exec bit,
  enforced via .gitattributes).
- IDE: `python pli_ide.py [file.pli]` or `pli-ide.bat` / `bin/pli-ide`.
- Regression: run every `pli/examples/*.pli` (skip `empdef.pli` — include
  member). Stdin fixtures: average=`3 1 2 3`, stage1=`1 2`,
  stage2=`X=1,Y=2;` + `AAAABBBB  111.25`. Delete `stage4_*.dat` and
  `pli/examples/sqldemo.sqlite` afterwards.
- Grammar check after parser edits:
  `python -c "from pli.parser import PLIParser; PLIParser().build(write_tables=False)"`
  (must build with no conflicts; delete any `parser.out`).

## Architecture (pli/ package, stdlib + ply only; IDE imports it, never reverse)
- `lexer.py` PLY lex. Case-insensitive; keywords ARE reserved (real PL/I
  has none — deliberate LALR(1) compromise). `EXEC SQL ...;` captured as
  one opaque quote-aware token. NOT spelled `¬ ^ ~`. Decimal literals
  with `.` become exact FixedDec; `nI` = imaginary; `nB` = binary.
- `parser.py` PLY yacc → AST (`nodes.py`, `_mk` factory, dispatch on
  `kind`). Multi-error compile via `stmt : error SEMI` panic recovery;
  all errors collected in `ParseError.messages` (cap 50). DECLARE
  attributes: unknown bare-ID attrs are compile errors against
  `KNOWN_ATTRIBUTES` (this catches `DCL MYNA ME CHAR(20)` typos).
- `interpreter.py` tree-walker. Control flow = Python exceptions:
  GotoSignal/ReturnSignal/LeaveSignal/IterateSignal/StopSignal +
  PLICondition (ON-conditions). `exec_block` catches PLICondition,
  dispatches to innermost established ON-unit (frames pushed per
  proc/BEGIN in `cond_frames`); normal ON-unit return resumes at next
  statement. NEVER let a broad `except` swallow these signals.
  Env chain: Environment parent links; procs are static-scoped; args
  by-reference when plain var/array/structure/member ref, else dummy.
  `global_env` holds SQLCODE etc. Implicit declare: I–N ⇒ FIXED else
  FLOAT (PL/I rule — undeclared vars are legal, not an error).
- `fixeddec.py` exact FIXED DECIMAL(p,q), N=15, PL/I F result-precision
  rules; int/int division deliberately NOT scale-preserving (documented
  deviation). Has __round__/__trunc__/__complex__ — needed by picture/
  builtins.
- `picture.py` PICTURE edit/validate; PicStr = str subclass carrying
  .num (so PUT shows edited text, arithmetic uses the number).
- `preproc.py` % preprocessor (single-pass; %DO unrolled; %GOTO forward
  only). CHARACTER pp-values substitute as RAW text — a string constant
  value must itself contain quotes.
- `sql.py` EXEC SQL runtime — see SQL section.
- Storage classes: BASED/POINTER are object-reference semantics, NOT
  byte overlay; UNSPEC works per-scalar via struct. BY NAME,
  DO REPEAT, REGIONAL files, cross-sections A(*,2): unsupported.
- v0.6.0 additions: separate compilation (run_files/run_multi concat
  top-level stmt lists; CLI takes multiple files; STATIC EXTERNAL
  shares via static_store key ("EXTERNAL", name) — EXTERNAL alone
  implies STATIC); LOCATE mode (PLIFile.pending flushed on next
  LOCATE/WRITE/CLOSE) and READ SET (finds the BasedVar whose ptr_ref
  names the SET pointer, else raw CHAR buffer); EVENT option on record
  I/O runs _exec_io on a thread, conditions stored on the EventValue
  and raised at WAIT; EXCLUSIVE file locks (per-key thread owner,
  released by REWRITE/DELETE/UNLOCK); ENV(REGIONAL(1)) = INDEXED with
  numeric keys; %PROCEDURE preprocessor functions (body statements
  WITHOUT %, own mini parser/exec in preproc.py; invocation name(args)
  substitutes RETURN value, result rescanned). Fixed: _arg_cell dummy
  decl for FixedDec/complex; _record_into resolves PtrRef targets.
  Example stage9.pli MUST run with stage9sub.pli (two files!).
- v0.5.0 additions: CHECK prefixes (interp.checked set, hook in
  assign_target, default print / ON CHECK raise); ONSOURCE/ONCHAR
  pseudo-vars + CONVERSION retry (interp._convert loop dispatches the
  ON-unit inline, c.fixed flag; assignment contexts only); ENTRY
  statement (SecondaryEntry subclass of Procedure with start index,
  registered in _register_proc; exec_block gained start=); BY NAME
  (Assign.byname attr); aggregate expressions (_array_binop +
  _apply_scalar_op; comparisons/bitops on arrays rejected);
  cross-sections via '*' in sub_list (PLIArray.cross_get/cross_set);
  SYSPRINT paging (line_no/page_size, ENDPAGE dispatched INLINE from
  _newline so the PUT resumes, LINENO/COUNT builtins, OPEN
  FILE(SYSPRINT) PAGESIZE(n)). GRAMMAR LESSON: the 0.3 format-rep
  rules were ambiguous (NUMBER '(' could be group or (expr)-count);
  restructured as format_rep_body where '(' after a count is always a
  group — adding unrelated rules can flip LALR conflict resolution.
- v0.4.0 additions: multiple assignment (Assign.target may be a list);
  DISPLAY/REPLY (reads stdin line); STATIC retention via
  interp.static_store keyed by id(DeclItem); GET COPY (echo) and
  GET STRING EDIT (string_input guards _read_chars); ONLOC (proc_stack
  + cond.loc set in dispatch)/ONFILE(qual)/ONKEY(source); prefix
  honoring via module-global _DISABLED (NOSIZE=truncate,
  NOSTRINGRANGE=clamp, SUBSCRIPTRANGE never disabled); ALLOCATE with
  bounds (alloc items are 3-tuples name/set_ref/bounds). Dropped by
  owner decision: 48-char set, label arrays.
- v0.3.0 additions: arrays of structures (PLIStructArray +
  StructMemberView for T.M(I)/M(I) distributed subscripts; INITIAL
  distributes across elements), INIT iteration factors ((10)0, (n)*
  skips), format repetition ((3)F(5), n(...), nested; expands in
  _expand_formats), multiple-closure END via MultiCloseLexer token
  filter in parser.py (injects synthetic END; tokens — can't be done
  in LALR; wraps the lexer in PLIParser.parse).

## Embedded SQL (pli/sql.py)
- Connections in `pli_dbc.json` (searched: program dir, cwd, ~).
  Drivers: `sqlite` (stdlib, offline tests) and `ibm_db` (Db2;
  `jdbc:db2://host:port/db` URL parsed into native DSN). Missing
  "password" ⇒ `interp.password_prompt` (getpass in CLI, dialog in IDE).
- Precompiler-layer statements: CONNECT TO/RESET, SET CONNECTION,
  SELECT INTO (+100/-811), DECLARE/OPEN/FETCH/CLOSE cursor, COMMIT/
  ROLLBACK, WHENEVER (SQLERROR/SQLWARNING/NOT FOUND ×
  CONTINUE/GOTO/STOP), INCLUDE SQLCA. Everything else passes through
  verbatim with :hostvar→? substitution — dialect = whatever the DB
  accepts. SQLCODE/SQLSTATE/SQLERRM set after every statement.
- Not implemented: indicator variables (NULL fetch ⇒ SQLCODE -305),
  PREPARE/EXECUTE dynamic SQL, WHERE CURRENT OF (cursors are
  client-side!), OUT params, scrollable cursors.
- ibm_db on Windows: import may fail with "DLL load failed" — sql.py
  self-heals by os.add_dll_directory(site-packages/clidriver/bin).
  User's Db2: localhost:25000/sample, user maga1, prompts for password.

## IDE (pli_ide.py, Tk, stdlib)
- Worker-thread run; GUI↔worker via out_queue tuples (kind first:
  out/input_req/passwd/done/err). GuiReader serves pre-typed SYSIN tab
  first, then live SYSIN> console; `/*` or EOF button ⇒ ENDFILE.
- Syntax highlight: single regex pass (comment | EXEC SQL | string |
  name | number | %), keyword/builtin sets imported FROM
  pli.lexer.reserved and pli.interpreter._BUILTINS — stays in sync
  automatically. Compile lists ALL errors, red-tags lines, double-click
  jumps. Find/replace Ctrl+F, F3. `--selftest` opens+closes for CI.

## Windows gotchas (learned the hard way)
- PowerShell pipes prepend UTF-8 BOM; stdin decoded as legacy codepage.
  __main__ reconfigures stdin utf-8-sig; GET paths lstrip ﻿.
- `python3` on Windows is a Store stub — bin/ scripts fall back to
  `python`. This machine: `python` = 3.14 (was 3.12 earlier).
- VS Code shows phantom errors on .pli files — that's IBM Z Open
  Editor's language server, unrelated to this interpreter.
- Heredoc `python - <<EOF` REPLACES piped stdin — never combine with
  a pipe when testing GET.
- Edits: BOM/invisible chars break Edit-tool matching; use chr(0xFEFF).

## Conventions
- DO NOT commit/push until the owner has personally tested the change
  and approved (rule set 2026-07-19 after a GUI fix passed headless
  tests but was still broken on screen). Make change -> automated
  tests -> STOP and ask owner to test -> then commit+push.
- Commits: imperative summary + body, footer
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`; push to main.
- Every feature lands with: example under pli/examples/, scripted test,
  full example regression green, README section updated.
- README.md at repo root is the single doc (no pli/README.md).

## Roadmap notes (agreed with owner, not yet started)
- Waiting: Rust port (plan exists — lalrpop/logos, Signal enum instead
  of exceptions, ODBC for Db2, conformance suite R0 first).
- Perf ladder when needed: parse-table cache → dispatch table → PyPy →
  transpile-to-Python backend (keep runtime lib) → only then native.
- SQL next steps by priority: indicator variables, EXECUTE IMMEDIATE,
  server-side cursors for WHERE CURRENT OF.
- "Large apps" gaps: separate compilation/external procedures, IDE
  debugger (statement loop makes stepping easy), batch test harness.
- Remaining Tier-1 language gaps after v0.3.0: multiple assignment
  (A,B = 0), GET STRING ... EDIT, label arrays, separate compilation.
- Executable output plan (planned 2026-07, not started; owner asked
  "unix executables as compiler output"):
  - Phase 0 (hours): shebang support — lexer skips leading #! line so
    `#!/usr/bin/env pli` + chmod +x makes .pli files executable.
  - Phase 1 (days): `pli-build prog.pli -o prog` via PyInstaller =
    self-contained single-file binaries (no Python needed on target);
    build per-OS via GitHub Actions release matrix. ~15-30MB, ~1s
    startup, NO speedup; ibm_db needs clidriver tree collected.
  - Phase 2: compose with the transpile-to-Python backend for speed.
  - Phase 3 (months, deferred): true native — emit C (setjmp/longjmp
    for GOTO/ON, ~5-8k-line C runtime) OR preferably via the Rust
    track: shared runtime, `plirs build` appending AST to interpreter
    binary (deno-compile trick) or emit-Rust + cargo. llvmlite saves
    nothing (runtime lib is the cost center).
  - Recommendation given: do 0+1 together (<1 week) when asked; defer
    3 until transpiler proves insufficient or Rust port is greenlit.
