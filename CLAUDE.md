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
  `"securityMechanism": "11"` on an ibm_db connection ⇒ Kerberos: DSN
  gets AUTHENTICATION=KERBEROS instead of PWD=, no password prompt;
  Windows SSPI/prior kinit supplies the ticket (typical for Db2 LUW —
  z/OS stays on password auth).
- Kerberos + SSL together (`"ssl": true` alongside securityMechanism
  11 — some Db2 LUW hosts mandate both, e.g. port 50200): ibm_db's
  native CLI driver has no GSKit keystore for SSL, so this combo
  routes through JDBC instead (jaydebeapi/JPype embeds a JVM;
  `_connect_jdbc_kerberos_ssl` in sql.py). Needs jaydebeapi+JPype1, a
  JVM, and an IBM Db2 JCC jar — `_find_jcc_jar` prefers DbVisualizer's
  bundled jar (JCC 4.32.28) over the IBM Data Server Driver's
  db2jcc4.jar (JCC 4.34.30 throws a DSS chained-parse error,
  ERRORCODE=-4499, against TLS-1.3-capable servers even when
  `-Djdk.tls.client.protocols=TLSv1.2` is forced); override via
  `"jdbc_jar_path"`. `_create_jaas_config` writes a JAAS login config
  pointing at the Kerberos ticket cache (Windows SSO or a prior
  `kinit`); `"realm"` in pli_dbc.json is the CALLER's own realm for
  the JAAS principal (e.g. "ALLIANZDE.ROOTDOM.NET") — NOT the DB2
  server's realm, which only goes in `"kerberosServerPrincipal"`
  (e.g. "db2agl1/host@SERVER.REALM"); swapping them still authenticates
  with a valid ticket but the server-side GSS handshake then fails
  with a GSSException. Cross-realm (user realm ≠ server realm, e.g.
  AD trust): `-Djavax.security.auth.useSubjectCredsOnly=false` lets
  Windows SSPI do the ticket referral (Java's own GSSAPI can't).
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
