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
  member; skip `sqldemo_db2.pli` and `sqldemo.pli` — both need a real
  Db2 (`sqldemo.pli` now targets a live internal ABS connection, not
  offline); skip `dli.pli` — needs a real IRIS instance, see DL/I
  section below). Stdin fixtures: hello=`World` (GET LIST NAME),
  average=`3 1 2 3`, stage1=`1 2`, stage2=`X=1,Y=2;` +
  `AAAABBBB  111.25`, javaext=`ROW=5 COL=5;`. stage9 needs
  stage9sub.pli passed alongside it (separate compilation). Delete
  `stage4_*.dat` afterwards.
  `pli/examples/include_fetch_demo/test_include_fetch.py` is a
  self-contained scripted test (builds its own sqlite fixture, runs
  itself, cleans up) — run standalone, not part of the plain-.pli sweep.
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
- `javagen.py` PL/I → Java transpiler (v0.12.0, experimental) — see
  Java transpiler section below.
- Storage classes: BASED/POINTER are object-reference semantics, NOT
  byte overlay; UNSPEC works per-scalar via struct (still true as of
  v0.7.0 — see below). DO REPEAT, REGIONAL(2/3), GENERIC: unsupported
  (BY NAME v0.5, REGIONAL(1) v0.6, cross-sections v0.5 — this line was
  stale, corrected 2026-07).
- v0.9.0 additions: %INCLUDE DB2 source-repository fallback (ai4pli/
  pli-tools-vscode's "recursive include resolver" concept, ported
  narrowly — this preprocessor is already a real recursive-descent
  process, so only the fetch-on-miss part was new; no BFS/queue/level-
  tracking needed, unlike ai4pli's own regex-scanner-bolted-onto-a-
  DB-extractor design). `preproc.py`'s `%INCLUDE` OSError branch now
  calls `Preprocessor._fetch_include_fallback` (lazily probes
  `pli_dbc.json` for an optional `"_source_repository"` block, cached
  on `self._db_fallback_cfg`, `False` sentinel = checked/not
  configured — zero behavior change when absent) before raising;
  delegates to new `pli/include_fetch.py`
  (`get_fallback_config`/`fetch_and_cache`/`IncludeFetchError`), which
  checks a local cache dir first (never re-fetches; the cache file
  being a real `.pli` file is what makes a fetched member's own nested
  `%INCLUDE`s recurse through the ordinary local-file path with no new
  logic), else queries `table WHERE name_column = ?` for one member at
  a time and restores CLOB newlines via `newline_char`. Any fetch
  failure raises `PreprocError` combining the original local-miss
  reason with the fetch-failure reason — never a silent skip.
  `sql.py`'s `SqlRuntime._connect` had its connection-building body
  factored into a module-level `connect_raw(cfg, key, config_dir,
  password_prompt)` (no `Interpreter`/`SqlRuntime` needed) so
  `include_fetch.py` reuses the exact same driver/Kerberos/SSL/JDBC
  logic (`_connect_jdbc_kerberos_ssl` etc.) with zero duplication;
  `_connect` is now a thin wrapper. Bundled adjacent fix: `preproc.py`
  had zero protection against circular `%INCLUDE` (crashed with a raw
  `RecursionError`) — `Preprocessor._include_depth` + module constant
  `_MAX_INCLUDE_DEPTH = 20` now raises a clean `PreprocError` instead;
  purely local, no DB2 involved, verified with a plain `A→B→A` cycle.
  `examples/include_fetch_demo/` demonstrates the whole path offline
  via the `sqlite` driver (own `pli_dbc.json`, `build_fixture.py`
  creates a `SOURCE_REPO` table mimicking DB2's CCMOBJT/CCMTYPT/QUELLE
  columns, `test_include_fetch.py` proves both the fetch-and-cache run
  and a cache-only run with the table emptied).
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

## DL/I support (pli/dli_preproc.py)
- `CALL PLITDLI(count, call-code, pcb-ptr, io-area [, ssa...])`
  intercepted in `exec_CallStmt` (checked BEFORE `_resolve_entry`, so it
  never touches the ENTRY-variable machinery — `DCL PLITDLI ENTRY
  EXTERNAL;` still declares a normal uninitialized ENTRY variable via
  the existing v0.7.0 path, it's just never resolved/called through).
  `count`/`pcb-ptr` are accepted but ignored — this bridge talks to
  exactly one database/PCB, mirroring how sql.py assumes "one current
  connection" unless SET CONNECTION is used.
- `IRISDLI` (in dli_preproc.py, adapted from the owner's reference
  `iris_dli.py`) treats one IRIS Global as a hierarchical DB: GU/GN/
  GNP/GHU/GHN/GHNP/ISRT/DLET/REPL, hold-locking via `db.lock`/`unlock`.
  REAL BUG found and fixed while integration-testing against the
  owner's live IRIS instance (localhost:1972, namespace SAMPLES, empty
  credentials): the IRIS Python driver's `.get()` returns `None` (not
  `""`) for both an unset leaf AND a branch node with only descendants
  — the reference code's `_exists`/`_get` assumed `""` was the only
  "no value" sentinel (same convention `_next_subscript` already
  normalizes), so `None != ""` made `_exists` wrongly report every
  nonexistent segment as present. Fixed by normalizing `_get()`'s
  result the same way `_next_subscript` already does.
  `DLIBridge.execute()` maps a call-code + SSA list onto one `IRISDLI`
  call; `ssa_subscript()` reduces each SSA CHAR value to one subscript
  level (bare name used as-is, `NAME(FIELD=VALUE)` contributes VALUE —
  the Global is positional, not field-addressed, so FIELD is
  documentation only).
- Lazy connection via `self.dlirt` (mirrors `self.sqlrt`'s lazy-import,
  lazy-connect pattern exactly): `dli_preproc.connect()` only imports
  `iris` on the first actual `CALL PLITDLI`, so the driver stays an
  optional dependency like ibm_db. Connection params come from
  `PLI_DLI_HOST/PORT/NAMESPACE/USERNAME/PASSWORD/GLOBAL` env vars, with
  credentials falling back to an interactive prompt — `_first()`'s
  `is None` checks (not truthiness) matter here since an empty IRIS
  username/password is a legitimate value, not "unset".
- Status of the last call surfaces via the `DLISTATUS` niladic builtin
  (`OK`/`GE`/`GB`/`GP`/`II`/`NOTHELD`/`NOLOCK`) — there's no PCB struct
  to read a status field from, unlike real DL/I.
- pli/examples/dli.pli: GU (hit + miss), GU into a child hierarchy then
  GN to the next sibling, ISRT + duplicate-ISRT rejection (II) — run
  live against the owner's IRIS instance via --diff-style manual
  verification (no sqlite-equivalent offline fallback exists for IRIS,
  unlike sql.py's `sqlite` driver — DL/I testing needs the real IRIS
  instance running).
- Origin note: this was first built in the stale pre-repo copy at
  `C:\Users\maga1\Documents\code\pli\` (a separate session, unaware of
  the "do not edit" note above) and even pushed to a new, unrelated
  `github.com/markons/pli` repo before the owner caught the mixup and
  asked for it to be ported here instead. `markons/pli` was NOT
  deleted (owner's choice) — it now has a stale, duplicate copy of
  this feature; this repo is the one to keep developing.
- NOT committed/pushed yet — awaiting owner test/approval per the
  standing convention above.

## Java transpiler (pli/javagen.py, v0.12.0, experimental — separate
## track from the roadmap's "transpile-to-Python" perf item, don't conflate)
- Real AST→Java source codegen (not an interpreter, not a serialized-AST
  runner) reusing pli's own lexer/parser (no separate grammar). Runtime:
  `javart/PLI.java` (I/O incl. 24-col PUT LIST tabs, string/bit/
  arithmetic/complex builtins, Event/threading, PLIError condition class).
  Driver: `scripts/build_java.py program.pli [-o dir] [--run] [--diff]`
  (`--diff` also runs `python -m pli` and byte-compares stdout, with
  \r\n normalized out first — Python's stdout is CRLF on Windows, Java's
  PrintStream never is, a platform artifact not a behavior diff — the
  actual validation method; all 10 example programs below pass this way,
  `javaext.pli` needs `--diff --stdin "ROW=5 COL=5;"` since it exercises
  `GET DATA`).
- v0.12.0: RECORD I/O (medium tier, item 2 of 6 — remaining: PICTURE,
  BASED/POINTER/CONTROLLED, exact FIXED DECIMAL, EXEC SQL; owner said
  "record i/o and picture are the next to go", overriding my own
  suggested order of BASED/POINTER/CONTROLLED next — PICTURE not yet
  started as of this entry).
  - Scope, deliberately narrower than the interpreter's full record I/O:
    CONSECUTIVE (one record per line) + INDEXED (in-memory `TreeMap`,
    persisted as `key\trecord` lines on CLOSE) only. `LOCATE` mode,
    `EXCLUSIVE` files, `ENV(REGIONAL(...))`, `EVENT(...)` on record I/O,
    `UNLOCK`, and `SET(...)` (BASED-variable READ) all raise a clear
    CodegenError rather than silently mistranslating — matches how
    structures scoped out arrays-of-structures/REFER/BY NAME last round.
  - `VarInfo` gained kind="file" + `file_indexed` bool. `jtype()` for
    "file" returns `"PLI.PLIFile"` with NO `[]` (a plain object
    reference, like "struct" — not scalar-boxed since CALL-by-ref on a
    FILE was never a requirement here). `_var_info_file()` parses
    `DCL f FILE [RECORD] [KEYED] [ENV(INDEXED)]`; ENV(REGIONAL)/
    EXCLUSIVE/STREAM/PRINT all CodegenError (STREAM/PRINT need no DCL
    at all in this backend — PUT/GET LIST/EDIT already target
    stdin/stdout directly).
  - REAL BUG caught before it ever ran (not by javac or --diff — by
    rereading `_emit_field` before writing the test): the first version
    emitted `new PLI.PLIFile(name)` for every FILE declare and never set
    `.indexed` on the instance, so EVERY file — including
    `ENV(INDEXED)` ones — would have silently behaved as CONSECUTIVE
    (Java's `boolean indexed` field defaults false). A class-body field
    initializer can't run an extra statement to set it after
    construction, so fixed via a small `PLI.mkFile(name, indexed)`
    static factory in the runtime instead of a second PLIFile
    constructor or an instance-initializer-block workaround.
  - `_leaf_width`/`_gen_record_from`/`_gen_record_into`/
    `_store_string_into_leaf` mirror the interpreter's `_leaf_width`/
    `_record_of_value`/`_fill_from_record` EXACTLY (verified by reading
    them first): CHAR/BIT left-justified to declared length or a 24/8
    default, FIXED right-justified width 12, FLOAT/COMPLEX/EVENT
    right-justified width 24 (COMPLEX/EVENT as a record leaf is an
    unlikely edge case, accepted as-is — `PLI.toStr` has no overload for
    them, so it would be a javac error, not a silent wrong answer).
    `gen_IOStmt` dispatches OPEN/CLOSE/READ/WRITE/REWRITE/DELETE;
    KEY/KEYFROM/KEY-for-DELETE all funnel through `_gen_key_text`
    (`PLI.toStr` for numeric operands, `.trim()` for CHAR/BIT — mirrors
    `_file_key`'s `to_string(v).strip()`, non-REGIONAL case only, the
    only case this backend supports).
  - REAL PRE-EXISTING BUG found via recio.pli, unrelated to record I/O
    itself: `'L00' || I` (CONCAT with a FIXED operand) generated
    `PLI.concat("L00", I[0])` — `PLI.concat(String,String)` doesn't
    accept a `long`, so this failed to compile. The interpreter's own
    CONCAT always stringifies BOTH operands via `to_string()` regardless
    of type (confirmed by reading `exec_BinOp`), so `'L00' || I` is
    valid PL/I that the Java backend had never actually exercised before
    (stage4.pli uses this exact idiom but was never run through
    build_java.py). Fixed via `_as_concat_string()` (long/double →
    `PLI.toStr`, boolean → `PLI.b`, string/bitstring as-is, else a
    CodegenError) threaded through `eval_BinOp`'s CONCAT case.
  - pli/examples/recio.pli: INDEXED (write 3, read by KEY, REWRITE,
    DELETE, sequential read with KEYTO bounded by a known count of 2 —
    NOT by ON ENDFILE, since ON-conditions still aren't supported here)
    + CONSECUTIVE (write 2, read back 2), reusing empdef.pli's EMP
    layout via `%INCLUDE` like stage4.pli. Verified byte-for-byte
    against `python -m pli` via --diff.
  - Full existing example suite (10 programs) re-verified after the
    CONCAT fix — all still byte-for-byte identical, no regressions.
  - NOT committed/pushed yet — awaiting owner test/approval per the
    standing convention; 4 medium-tier items remain (PICTURE,
    BASED/POINTER/CONTROLLED, exact FIXED DECIMAL, EXEC SQL).
- v0.11.0: STRUCTURES (medium tier, item 1 of 6 — remaining: PICTURE,
  BASED/POINTER/CONTROLLED, record I/O, exact FIXED DECIMAL, EXEC SQL;
  owner asked to "go on with the medium features", this was landed as
  its own checkpoint rather than pushing through all 6 unreviewed).
  Pure pli/javagen.py + 2 new examples, ZERO changes to javart/PLI.java
  or the interpreter — verify via `git status --short` if this claim
  ever looks stale.
  - `StructType` (java_class_name, members dict name->VarInfo-or-
    StructType, order list, spec=(level,subitems) kept for LIKE) is the
    codegen-side analog of PLIStructure. `VarInfo` gained kind="struct"
    + `struct_type`; jtype() for a struct returns the class name with
    NO `[]` — structures are already Java reference types, no
    1-element-array boxing needed for CALL-by-ref (unlike every other
    kind).
  - `group_declares()` mirrors exec_Declare's flat-list level-1/level-N
    grouping EXACTLY (verified by reading it first, not guessed) —
    needed because collect_declares() flattens ALL DeclItems across
    every DCL statement in a procedure into one list with no structure
    grouping at all; this was true before too but only mattered once
    structures existed.
  - CRITICAL DESIGN POINT, cost a real compile error to discover: Java
    classes are nominal-typed, but PL/I structure PARAMETERS are matched
    STRUCTURALLY (the callee just redeclares the shape it expects — see
    interpreter.py's `_declare_structure_inner`: "structure parameter:
    keep the caller's structure", no deep validation). Naively
    generating one fresh Java class per DCL occurrence means a
    structurally-identical parameter redeclaration gets an INCOMPATIBLE
    type from the caller's variable — `new GIVE_RAISE().invoke(EMP, ...)`
    failed to compile ("_Struct1 cannot be converted to _Struct5") the
    first time through. Fixed with `_struct_signature()` (level + each
    member's resolved VarInfo fields, recursive, hashable — built from
    RESOLVED types, not raw AST nodes, since two separate DeclItem
    parses of literally the same text are different object instances)
    + `self.struct_cache` (signature -> StructType) in `_new_struct_type()`,
    so identical shapes — whether from LIKE, independent DCLs, or a
    parameter's local redeclaration — share ONE generated class. This
    also means `LIKE` and its target automatically end up as the same
    class for free (no separate LIKE-aliasing logic needed).
  - Struct-type class bodies are generated into a temporarily redirected
    `self.lines`/`self.indent` buffer (so the existing indent-aware
    `self.w()` machinery just works), then appended to `self.struct_defs`
    and spliced into the final output at a saved position (right after
    main(), before the procedure classes) once ALL procedures have been
    generated — struct declarations are discovered mid-procedure, so
    they can't be emitted in their final position until everything else
    is done.
  - Qualified/unqualified/partial-qualification member resolution is
    ALL compile-time (unlike the interpreter, which resolves `.find()`/
    `_search_member()` at runtime): `_resolve_struct_node()` walks a
    Ref/Member AST node down to (java_text, VarInfo-or-StructType);
    `_find_in_struct()` mirrors PLIStructure.find (direct member wins
    immediately, else a UNIQUE recursive nested-match, ambiguous ==
    CodegenError); `_search_unqualified()`/`_search_unqualified_in()`
    mirror `_search_member` EXACTLY including its "innermost scope with
    ANY hit wins" rule (ambiguity is only checked within that one scope
    level, not across the whole chain) — ctx.parent-chained, same
    mechanism as plain-variable lookup.
  - REAL BUG (not just a gap) found via structest2.pli: the first
    `_search_unqualified_in` iterated `syms_dict.values()` and returned
    `_find_in_struct`'s path AS-IS — but that path is relative to the
    STRUCTURE TYPE, missing the structure VARIABLE's own name at the
    front. `ID = 42;` (meaning REC.ID) silently compiled to a top-level
    `ID[0] = 42L;` referencing a field that doesn't exist — caught by
    javac ("cannot find symbol ID"), not by any Python-side check. Fixed
    by iterating `.items()` and prepending the variable name to the path.
  - Structure assignment (`_gen_struct_assign`) mirrors
    interpreter._assign_structure: struct=struct is leaf-by-leaf
    (arrays copied element-wise, scalars direct), same shape (leaf
    count) required; struct=scalar broadcasts to every leaf but — Java
    static typing forces a NARROWER rule than PL/I's per-leaf automatic
    conversion — every leaf must share the source's Java kind, or a
    clear CodegenError, not a javac type error or silent wrong output.
  - PUT LIST on a whole array or whole structure unrolls to individual
    elements/leaves AT CODEGEN TIME (`_flatten_put_list_item`, sizes are
    known constants for concrete arrays), matching the interpreter's
    runtime `list(arr.data)` / `leaf_values()` — NOT a runtime loop.
    First version of this had a bug: it called the array-flattening
    helper unconditionally on every struct leaf, including SCALAR
    leaves, which don't need `[idx]` flattening, just a `[0]` value
    access — wrongly treated every scalar leaf as an "unsupported
    assumed-size array". Fixed by branching on `linfo.is_array` first.
  - `CALL P(EMP.SALARY)` (a structure MEMBER passed by reference) now
    works too, not just a bare structure variable: `_byref_arg_text()`
    generalizes the old bare-Ref-only by-ref check through
    `_resolve_struct_node`, stripping a trailing `[0]` to get the
    box/array reference instead of the value (structures/arrays already
    have no `[0]` to strip, used as-is).
  - Scope boundary, deliberate, not yet fixed: `_sym()` (used by GET
    LIST, HBOUND, WAIT, CALL EVENT, etc.) does NOT fall back to
    unqualified member search — its callers independently do
    `sanitize(name)` assuming the name IS the java field directly, so
    extending _sym alone without updating every call site would produce
    a subtly wrong (unqualified, not the resolved qualified path) text.
    Given time constraints this was left as a narrow, documented gap
    rather than threading the fix through every caller.
  - Scope cuts for this round (CodegenError, named clearly): arrays of
    structures, BASED/CONTROLLED structures, REFER, structure aggregate
    expressions (S3=S1+S2), BY NAME assignment, whole-array/whole-struct
    refs in a GET DATA list (PUT DATA supports them; GET DATA doesn't).
  - pli/examples/structest.pli (nested structs, LIKE, qualified/partial
    access, struct assignment, struct CALL param) and structest2.pli
    (unqualified member write+read, array member inside a structure,
    CALL passing a structure MEMBER by reference) — both verified
    identical between python -m pli and the Java backend via --diff.
  - NOT committed/pushed yet — this is a checkpoint pause, not a stop;
    5 more medium-tier items remain (PICTURE, BASED/POINTER/CONTROLLED,
    record I/O, exact FIXED DECIMAL, EXEC SQL), each comparable in size
    to structures alone or larger (SQL especially). Deliberately landed
    as a separate, reviewable unit rather than one giant multi-feature
    diff.
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
  CONTROLLED/DEFINED/UNSPEC, record I/O, exact FIXED DECIMAL (literals
  become IEEE double — no FixedDec/BigDecimal here), SQL, BEGIN blocks,
  GET EDIT, labels/DCLs nested inside DO/IF/SELECT/BEGIN, assumed-size
  `(*)` within a multi-dim array (only a whole 1-D `(*)` param), array
  elements in a GET DATA list (PUT DATA supports them; GET DATA is
  scalars-by-name only).
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
- v0.10.0 additions ("easy tier" from the owner's own capability
  assessment; owner explicitly said proceed without asking permission
  again this round):
  1. Multi-dim arrays + non-1 lower bounds + aggregate assignment.
     VarInfo gained `dims` ([(lo,hi),...] ints, None = scalar or the
     pre-existing 1-D assumed-size (*) param case, unchanged). One flat
     Java array backs any dimensionality (row-major, matches
     PLIArray._offset in interpreter.py EXACTLY: off=off*extent+idx per
     dimension in order — verified by reading that method, not
     guessed). New `_subscript_index()`/`PLI.idx(i,lo,hi)`/
     `PLI.idx1(i,len)` replace the old unchecked `sub-1`; SUBSCRIPTRANGE
     now a real thrown condition instead of a raw Java
     ArrayIndexOutOfBounds. Aggregate assignment (`_gen_aggregate_assign`)
     evaluates the RHS exactly ONCE into a temp before the fill loop —
     critical: naively re-embedding the RHS Java text inside the loop
     body would re-execute a side-effecting RHS (e.g. a function call)
     N times instead of once. HBOUND/LBOUND/DIM take the optional
     dimension-number 2nd arg now.
  2. COMPLEX. PLI.Complex (re,im doubles) + cAdd/cSub/cMul/cDiv/cNeg/
     cAbs/cConjg; "complex" is a 4th expr()-kind (long/double/string/
     bitstring were the others) with auto-promotion in
     `_complex_binop`/`_compare` (bare long/double operand → PLI.C(x)).
     REAL/IMAG/CONJG/COMPLEX/ABS(complex) special-cased in
     `_builtin_or_call` BEFORE the generic _BUILTIN_MAP lookup (ABS
     needs to return "double" for a complex arg, not the generic
     same-kind-as-arg rule). Imaginary literals (already lexed as
     Python `complex` by lexer.py's `nI` suffix) handled in eval_Num.
  3. Multitasking. PLI.Event (CountDownLatch-backed) + spawnTask (runs
     a Runnable lambda on a Thread, sets ev.status=1 on exception) +
     wait(events[], n) (polling loop, 5ms sleep) + completion/status.
     `CALL p(...) EVENT(e)` → `e[0]=new PLI.Event(); PLI.spawnTask(e[0],
     () -> { new P(...).invoke(...); });` — the lambda body's `new P()`
     still correctly captures the enclosing instance via Java's normal
     rules (lambdas are transparent to `this`/outer-instance capture,
     unlike anonymous classes), so a worker task nested inside the
     caller can freely mutate the caller's fields, exactly like
     stage5.pli's WORKER→TOTAL pattern. KNOWN, ACCEPTED hazard: two
     tasks both mutating the same shared field with no synchronization
     is a genuine data race with REAL OS threads (worse than the Python
     backend's GIL-cushioned version) — this is faithful to actual
     PL/I multitasking risk, not a new deviation, so NOT synchronized
     on purpose; javaext.pli's test deliberately has each task write to
     its own RESULTS(slot) to keep --diff deterministic rather than
     relying on interleaving-sensitive shared-accumulator output.
  4. PUT/GET DATA, PUT/GET STRING, FORMAT + R() + (n)FMT repetition.
     PUT DATA supports scalars AND array elements (dynamic subscript
     baked into a Java string-concat expression for the "NAME(i)="
     label); GET DATA is scalars-only, via PLI.getDataMap() (reads to
     `;`, regex NAME=value pairs into a Map, order-independent — NOT
     positional). PUT/GET STRING via a capture-stack in PLI.java
     (beginStringCapture/endStringCapture push/pop a StringBuilder +
     its own column counter so PUT LIST tab math stays correct inside
     a capture; pushStringInput/popStringInput do the same for GET
     STRING's token source). FORMAT statements collected per-procedure
     into `Ctx.format_defs` (NOT chained to the parent — a FORMAT label
     is only visible in its own procedure, matching GOTO's top-label
     restriction); `_expand_formats()` inlines R(label) and unrolls
     (n)FMT groups (constant n only) before codegen, mirroring the
     interpreter's own `_expand_formats`.
  5. Real bugs found and fixed while building the above (all verified
     via --diff, not assumed):
     - Ctx/scope chaining was MISSING entirely before this round: a
       nested procedure's `self.ctx.syms` only ever held ITS OWN
       params/locals, so referencing an enclosing procedure's variable
       (e.g. SUMTO writing to JAVAEXT's RESULTS array) raised "not
       declared". Fixed by making Ctx parent-chained (`Ctx.lookup`
       walks up) AND, just as importantly, restructuring
       gen_proc_class so a procedure's OWN Ctx is created and set as
       `self.ctx` BEFORE its nested procedures are generated (it used
       to be created only much later, around the invoke() body, so
       nested classes were generated against the WRONG (grandparent or
       None) ctx). top_labels and format_defs stay per-procedure,
       unchained on purpose (real PL/I GOTO/FORMAT-label scoping).
     - `_gen_put_edit` treated every format item as consuming one data
       item in lockstep — wrong the moment a control item (X/COL/SKIP)
       appears in the list, since those must NOT consume a data item.
       Only surfaced once R()/FORMAT was added (no prior test had X()
       interleaved between data formats). Fixed to mirror the
       interpreter's exact algorithm: an inner while-loop consumes
       leading control formats before each data item. COL was ALSO
       added for real (PLI.editCol) rather than left unsupported,
       since the fix made it nearly free.
     - `_gen_store`/`_gen_aggregate_assign` discarded the RHS's
       expr()-kind and never converted a Java `boolean` (from a
       comparison, or now COMPLETION()) into a BIT/CHAR string — so
       `FLAG = (X > 5);` was ALREADY silently broken before this round
       (pre-existing, not something this round introduced) and
       COMPLETION() would have hit the identical gap. Fixed via
       PLI.b(boolean)->String + `_bool_to_string()`, threaded through
       both call sites.
     - `STOP` → `System.exit(0)` bypasses main()'s try/finally, so the
       final `PLI.flushLine()` never ran — trailing newline silently
       dropped whenever a program's last statement was STOP. Fixed by
       flushing explicitly right before the exit() call in gen_Stop.
  6. pli/examples/javaext.pli added (all 6 features in one program,
     avoids BEGIN blocks — not supported — declares/nested SUMTO proc
     directly in JAVAEXT's body instead, same shape as stage5.pli).
  7. NOT committed/pushed yet — awaiting owner test/approval per the
     standing convention above (this round moved faster since the
     owner pre-authorized "don't ask permission again", but that was
     about tool-call confirmations, not the separate commit-approval
     gate, which still stands).

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
