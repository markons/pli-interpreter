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
- `javagen.py` PL/I → Java transpiler (v0.9.0, experimental) — see
  Java transpiler section below.
- Storage classes: BASED/POINTER are object-reference semantics, NOT
  byte overlay; UNSPEC works per-scalar via struct (still true as of
  v0.7.0 — see below). DO REPEAT, REGIONAL(2/3), GENERIC: unsupported
  (BY NAME v0.5, REGIONAL(1) v0.6, cross-sections v0.5 — this line was
  stale, corrected 2026-07).
- v0.7.0 additions (object-graph features; NOT byte-accurate storage
  — see roadmap note below, that's a separate, larger effort):
  REFER self-defining structures (bound tuple ("REFER",expr,name);
  _resolve_bound prefers parent.members[name].value, else evals expr
  in env, writes back into the sibling; wired into _make_entry (now
  takes parent=), _build_members, _declare_struct_array, ALLOCATE
  bounds). Structure aggregate expressions (_struct_binop +
  _clone_struct_shape, mirrors _array_binop; wired into eval_BinOp/
  eval_UnOp ahead of the PLIArray check). ENTRY variables (EntryValue
  wrapper class; Decl base "ENTRY"; _declare_one only creates a real
  variable when no Procedure already exists under that name, else
  stays descriptive as before; exec_Assign peeks the target via
  _is_entry_target/_eval_entry_expr so `F = PROC;` captures the entry
  instead of calling it; _resolve_entry unwraps for CALL and for
  eval_Ref's Variable+args branch). SCOPED OUT: GENERIC entry
  selection and passing a bare procedure name to an ENTRY-typed
  parameter — both documented as not implemented, not silently
  dropped. AREA/OFFSET/EMPTY (Area class = logical pool, leaf-count
  budget via _alloc_size, id()-keyed in interp.area_of; OFFSET decl
  base reuses Pointer/NULL machinery — EMPTY() aliases NULL(); ALLOCATE
  gained IN(area), alloc_item is now a 4-tuple
  (name,set_ref,bounds,area) — check any code iterating stmt.items).
  Grammar note: ref/sub_list already accepted '*' pre-0.7 for
  cross-sections; REFER reuses the plain <bound> nonterminal so no new
  ambiguity. stage10.pli demonstrates all four (member name DATA is
  reserved — use a different name in examples).
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

## Java transpiler (pli/javagen.py, v0.9.0, experimental — separate
## track from the roadmap's "transpile-to-Python" perf item, don't conflate)
- Real AST→Java source codegen (not an interpreter, not a serialized-AST
  runner) reusing pli's own lexer/parser (no separate grammar). Runtime:
  `javart/PLI.java` (I/O incl. 24-col PUT LIST tabs, string/bit/
  arithmetic builtins, PLIError condition class). Driver:
  `scripts/build_java.py program.pli [-o dir] [--run] [--diff]`
  (`--diff` also runs `python -m pli` and byte-compares stdout — the
  actual validation method; all 6 example programs below pass this way).
- Repr: every scalar is a 1-element Java array (long[1]/double[1]/
  String[1]) so CALL-by-ref is plain aliasing (mirrors interpreter's
  Variable box); PL/I arrays → plain Java arrays (already refs, no
  double-boxing — VarInfo.jtype() always appends exactly one `[]`
  regardless of scalar vs array, this was a real bug once: `is_array`
  must NOT gate whether `[]` is added). Nested PL/I procs → non-static
  Java inner classes; Java's automatic implicit-outer-instance capture
  reproduces lexical scoping for free — recursive self-calls and
  sibling/enclosing calls need ZERO special-casing at the call site
  (`new X(...)` inside a method always captures the correct outer
  instance from where the `new` textually sits). GOTO restricted to
  labels at a procedure's OWN top level (not nested in DO/IF/SELECT):
  compiles to a `switch(pc)` inside `while(true) dispatch: {...}`;
  ordinary fallthrough = normal label flow, `pc=N;continue dispatch;`
  = GOTO. Trailing `throw ... fell off end` safety net is emitted only
  when reachable — a static `always_returns()`/`body_always_returns()`
  check skips it for label-free bodies provably ending in RETURN/STOP
  (needed because javac hard-errors on statically-unreachable code;
  the dispatch-loop case always keeps it since its `break;` after the
  switch is genuinely reachable).
- Type system gotcha (cost real debugging time): CHAR and BIT both
  resolve to VarInfo.kind "string", but expr()-level kind has a THIRD
  tag "bitstring" for expression results (Ref to a BIT var, BIT literal,
  bitAnd/Or/Not) — needed so `&`/`|`/`^`/PUT LIST route through
  PLI.bitAnd/bitOr/bitNot/asBits instead of the boolean/CHAR paths.
  Do NOT reintroduce the earlier `_is_bit_expr(AST node)` pattern-match
  approach — it silently breaks on any compound expression (e.g. `^B1`
  lost its bit-ness because UnOp isn't a bare Ref/Bits node); kind must
  propagate through eval_UnOp/eval_BinOp/eval_Ref properly instead.
  REPEAT(s,n) is n+1 copies, COPY(s,n) is n copies — do not merge them.
  Procedure-call-as-expression return type comes from a pre-pass
  (`_collect_ret_kinds`, walks all top-level + nested ProcDefs before
  any codegen) into `self.ret_kinds`; a BIT/CHAR-returning function
  used in `IF f(...) THEN` needs this to know to route through
  PLI.bitTrue rather than defaulting to `!= 0` on a String.
- Scope cuts (raise CodegenError naming construct+line, never silently
  wrong): structures, PICTURE, ON-conditions, BASED/POINTER/
  CONTROLLED/DEFINED/UNSPEC, record I/O, COMPLEX, exact FIXED DECIMAL
  (literals become IEEE double — no FixedDec/BigDecimal here), SQL,
  multitasking, multi-dim arrays, non-1 array lower bounds, aggregate
  assignment, labels/DCLs nested inside DO/IF/SELECT/BEGIN.
- FIXED (was open in the previous entry below): `STRING` reserved word
  collided with `DECLARE STRING CHAR(*);` (broke the owner's real
  match.pli, a `string`-parameter glob-matcher). Fix in lexer.py's
  t_ID: STRING only tokenizes as STRINGKW when the next significant
  char (skip ws + `/* */`, via new `_peek_lparen` static helper) is
  `(` — true for all 3 real uses (PUT STRING(, GET STRING(, the
  STRING(...) builtin), false for a bare declaration. Verified: match.pli
  parses+runs now; stage2.pli's PUT STRING/GET STRING round-trips still
  work (regression-checked, not just assumed). Grammar rebuild clean.
  General pattern for any FUTURE keyword/identifier collision like this.
- pli/examples/match_demo.pli (added for backend validation) is a
  self-contained 3-external-procedure copy of the owner's match.pli
  (MAIN driver + recursive `match` + sibling `char_in_class`), with
  the colliding parameter renamed `string`→`txt` (predates the STRING
  fix above; could use the real name now, left as-is, harmless).
- The owner's actual `C:\Users\maga1\Documents\code\match.pli` (OUTSIDE
  this repo — the "stale pre-repo copy" note at the top is about
  `.../code/pli/`, unrelated) now has, in the same file: a `char_in_class`
  external proc + a `MATCHTEST: PROC OPTIONS(MAIN);` driver with 8 test
  cases, appended after `end match;`. Running it (after the STRING fix)
  surfaced TWO real, pre-existing bugs in the owner's own MATCH
  algorithm (not bugs in either back end — diagnosed, not fixed, it's
  the owner's call):
  1. `declare class char(256);` (no VARYING) — same truncation-to-256
     issue as match_demo.pli above: character-class `[XYZ]` matching
     always fails. Fix would be adding VARYING.
  2. Unclosed-bracket case (`'A[BC'`, no closing `]`) raises an
     UNHANDLED StringRange condition instead of the documented
     "unclosed '[' makes the match fail" (return '0'B) — root cause:
     the loop guard `k <= length(pattern) & substr(pattern,k,1) ^= ']'`
     assumes `&` short-circuits. Real PL/I(F) does NOT guarantee
     short-circuit evaluation of `&`/`|` (well-known F-level trivia),
     and this interpreter evaluates both operands — matching historical
     PL/I(F) behavior, not a bug here. Once k runs past length(pattern),
     the right operand's SUBSTR goes out of range. A fix would need an
     explicit nested IF to short-circuit manually, not just `&`.
  3 of 8 driver cases pass as expected; the crash on case 6 means cases
  7-8 never execute (no ON CONDITION established — expected default
  PL/I behavior, program aborts on the unhandled condition).
- Owner manually compiled/ran a build (Strings.java + PLI.java) before
  approving; committed/pushed/tagged as v0.9.0.

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
- Remaining gaps after v0.7.0: byte-accurate BASED/DEFINED storage
  overlay (the one feature that does NOT fit the current object-
  reference model — needs a bytearray-backed storage representation
  living alongside PLIStructure/Variable, scoped narrowly to BASED/
  DEFINED rather than all storage; this is the recommended next big
  effort), GENERIC entry selection, iSUB defining, DO REPEAT,
  REGIONAL(2/3), sterling/scaled-exponent PICTUREs, 48-char set and
  label arrays (deliberately dropped by owner).
- Executable output: Phase 0+1 DONE in v0.8.0 (CLI-only scope, per
  owner — IDE not packaged). _strip_shebang() in interpreter.py
  (module-level fn, called from run_multi before preprocess) blanks a
  leading #! line, preserving line numbers.
  scripts/build.py has TWO modes (owner explicitly wanted per-program
  compilation, not just a frozen interpreter — this was a real
  misunderstanding mid-build, corrected once flagged):
    1. `build.py program.pli [more...] [-o name]` — PER-PROGRAM
       COMPILE, the main use case. Reads the source(s), calls
       pli.preproc.preprocess() ONCE at build time (fully expanding
       %INCLUDE/%DO/%PROC — this is legitimate since the preprocessor's
       whole job is "produce final source text," doing it once at
       build time instead of every run is strictly correct), embeds
       the expanded text via repr() into a generated
       build/pyinstaller/<name>/_program_entry.py that calls
       interp.run_multi(SOURCES) directly with NO argv handling — the
       resulting exe is fully standalone/argument-free. Multi-file
       args = separate-compiled units, same as run_files. Verified:
       %INCLUDE baked in (ran copied exe from %TEMP%, no access to the
       include member, still worked), multi-file (stage9+stage9sub),
       and real SYSIN input via a piped run of the built average.exe
       (the build script's own smoke test uses EMPTY stdin + 15s
       timeout + does NOT fail the build on nonzero exit, since that's
       the compiled PROGRAM's business, not the build's — only "exe
       not found" after PyInstaller runs fails the build).
    2. `build.py` (no args) — the original generic reusable
       interpreter, `pli.exe program.pli` for ANY program afterwards;
       unchanged from the first pass, this is what populates the
       Releases page via CI.
  scripts/pli_cli_entry.py is the PyInstaller entry for mode 2
  (imports pli.__main__ as an absolute import so relative imports
  inside the package resolve — do NOT point PyInstaller at
  pli/__main__.py directly, that breaks relative imports; same
  ROOT-sys.path.insert trick used in the generated per-program entry).
  write_tables=False in parser.py already avoids PLY writing
  parsetab/parser.out into the frozen temp dir (verified no issue).
  .github/workflows/release.yml only builds mode 2 (windows-latest +
  ubuntu-latest matrix, triggers on v* tag push, uploads to the
  GitHub Release via softprops/action-gh-release) — mode 1 has no CI
  hook, it's a local/on-demand developer tool. Verified locally on
  Windows: interpreter exe 25MB, per-program exes similar; runs from
  an unrelated cwd; %INCLUDE/separate-compilation/EVENT-IO/
  sqlite-EXEC-SQL all work frozen in both modes. Deliberately NOT
  bundled: ibm_db (redistribution/size). NOT built/verified: Linux
  binary (only via CI once pushed — no Linux machine available here),
  macOS.
  - Phase 2 (still open): compose with the transpile-to-Python backend
    for speed once that exists.
  - Phase 3 (still deferred): true native (C or, preferably, via the
    Rust track if greenlit — see below).
