# pli — a PL/I(F)-level interpreter in Python (built on PLY)

A tree-walking interpreter for a large subset of IBM PL/I at roughly the
F-compiler language level, using [PLY](https://www.dabeaz.com/ply/)
(`ply.lex` / `ply.yacc`) for the lexer and LALR(1) grammar — plus a small
stand-alone Tkinter IDE.

## Install

Two ways to get running — pick one.

**A. Prebuilt executable (no Python needed).** Download a
self-contained CLI executable from the [Releases page](../../releases)
(Windows and Linux, x64):

```
pli.exe program.pli        # Windows
./pli program.pli          # Linux
```

Runs the sqlite `EXEC SQL` backend out of the box. `ibm_db` (Db2) and
DL/I (`CALL PLITDLI`, needs the IRIS driver) are **not** bundled — use
the source install below for those.

**B. From source.** Requires Python 3.10+:

```bash
git clone https://github.com/markons/pli-interpreter.git
cd pli-interpreter
pip install -r requirements.txt      # installs ply
```

That's the whole interpreter. Everything else is opt-in, only needed
if you actually use the corresponding feature:

| Feature | Install |
|---|---|
| Db2 `EXEC SQL` | `pip install ibm_db` |
| DL/I `CALL PLITDLI` | `pip install intersystems-irispython` |
| Building standalone executables | `pip install pyinstaller` |

sqlite `EXEC SQL` needs nothing extra (stdlib). See `requirements.txt`
for the same list as commented-out lines.

Verify it works:

```bash
python -m pli pli/examples/hello.pli
```

On **Linux/macOS/WSL**, recent distros refuse `pip install` into the
system Python (PEP 668, "externally-managed-environment"); use a venv:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m pli pli/examples/hello.pli
```

On Debian/Ubuntu (including WSL), `sudo apt install python3-ply` works
too and skips the venv entirely — fine for just running the
interpreter, though it ties you to whatever `ply` version Ubuntu
packages, and won't cover the *optional* extras below, which still
need `pip`.

On **WSL** specifically, clone into your Linux home directory (e.g.
`~/pli`) rather than working under `/mnt/c/...` — a Windows drive
mounted via `drvfs` can silently break the venv's `pip` launcher. See
[WSL.md](WSL.md) for the full setup (including that workaround and
building a native Linux binary with `scripts/build.py`).

## Usage

Run a program from the repo root:

```
python -m pli pli\examples\hello.pli
```

On Windows, `pli.bat` does the same from any directory:

```
pli.bat myprog.pli
```

On Linux/macOS use the `bin/` launchers (add `bin/` to your `PATH`
for plain `pli myprog.pli`), or the root-level `.sh` scripts if you'd
rather run from the repo root without touching `PATH` — both mirror
the `.bat` files 1:1 and do the same thing:

```
bin/pli myprog.pli          # or: ./pli.sh myprog.pli
bin/pli-ide myprog.pli      # or: ./pli-ide.sh myprog.pli
```

Several files as one separately-compiled program (external procedures,
`STATIC EXTERNAL` shared data):

```
python -m pli main.pli sub1.pli sub2.pli
```

### Runtime PARM

A main procedure may declare one optional `CHAR VARYING` parameter to
receive a runtime parameter string, mirroring the mainframe convention
of `PARM=` on the JCL `EXEC` card:

```
MAIN: PROC(PARMSTR) OPTIONS(MAIN);
   DECLARE PARMSTR CHAR(100) VARYING;
   ...
END MAIN;
```

Supply it on the command line with `--parm` (it can appear anywhere
in `argv`, before or after the source file list, and works unchanged
through `pli.bat`/`bin/pli`):

```
python -m pli main.pli --parm 'ABC 123'
```

If `OPTIONS(MAIN)` declares no parameter, `--parm` is simply not
needed. Declaring more than one parameter on the main procedure is an
error. See `pli/examples/parmdemo.pli`.

Programmatic API:

```python
from pli import run_source, run_file
run_source("H: PROC OPTIONS(MAIN); PUT LIST('HI'); END H;")
```

Example programs live in `pli/examples/` — `stage1.pli` … `stage10.pli`
exercise the F-level-and-beyond features; the rest are basics. Try
`hello.pli` first, then `fibfact.pli` (recursion) or `sort.pli`
(arrays, by-reference CALL).

## The IDE

```
python pli_ide.py [program.pli]      # or double-click pli-ide.bat
```

On Linux/WSL, Tk is a separate OS package (not pulled in by `pip`):
if this fails with `ModuleNotFoundError: No module named 'tkinter'`,
run `sudo apt install python3-tk` (Debian/Ubuntu) first. The
interpreter itself (`python -m pli`, `scripts/build.py`) has no
tkinter dependency.

A stand-alone Tkinter IDE (stdlib only): editor with line numbers,
PL/I syntax highlighting and find/replace (Ctrl+F, F3); **Compile**
(F7) lists *all* syntax errors at once (panic-mode recovery) with
click-to-jump line highlighting; **Run** (F5) executes on a worker
thread with live SYSPRINT output, a pre-supplied SYSIN tab, a
"Parameter (PARM)" tab holding the runtime PARM string for a main
procedure declared `PROC(parmvar) OPTIONS(MAIN)` (the GUI equivalent
of the CLI's `--parm`, see *Runtime PARM* above), and an
interactive `SYSIN>` console for `GET` input (`/*` or the EOF button
signals ENDFILE); **Build EXE...** freezes the current program into a
standalone executable via `pli-build` (see *Building standalone
executables* below), prompting for a save location. The interpreter
itself has no dependency on the IDE.

## Supported language

**Program structure** — main procedure with `OPTIONS(MAIN)`, nested
procedures (recursive, parameters, `RETURNS`), `BEGIN` blocks, labels,
condition prefixes `(SIZE):`. The main procedure may declare one
optional `CHAR VARYING` parameter to receive the runtime PARM string
(mainframe JCL convention): `MAIN: PROC(PARMSTR) OPTIONS(MAIN);` —
see *Runtime PARM* below.

**Data** — `FIXED`/`FLOAT` `BINARY`/`DECIMAL` with precision `(p,q)`,
`CHAR(n)` (padded) and `VARYING`, `BIT(n)`, `PICTURE`/`PIC` (numeric
editing incl. `Z * V . , B S + - $ CR DB` with drifting characters, and
character pictures `A X 9`), `COMPLEX` (with `3+4I` constants),
`POINTER`, `LABEL`, `EVENT`, arrays with arbitrary bounds and `(*)`
parameters, **structures** with level numbers, `LIKE`, qualified and
partially qualified names, structure assignment and by-reference
structure arguments, **arrays of structures** (`DCL 1 T(10), 2 ...`)
with element access `T(I).M`, distributed subscripts `T.M(I)` / `M(I)`,
and aggregate assignment. `INITIAL` supports iteration factors:
`INIT((10)0, (5)'AB', (3)*)` — `(n)*` skips n elements; in arrays of
structures the INITIAL list distributes across elements (PL/I rule).
Implicit declaration follows the I–N rule.

**Exact fixed-point arithmetic** — decimal literals are exact
`FIXED DECIMAL` values; `+ - * /` follow the PL/I F result-precision
rules with N = 15 (`0.1 + 0.2 = 0.3` is `'1'B` here), raising
`FIXEDOVERFLOW`/`SIZE`. (Deviation: integer/integer division returns an
integer or float instead of a scaled fixed value.)

**Storage classes** — `AUTOMATIC` (default), `STATIC` (accepted),
`CONTROLLED` with `ALLOCATE`/`FREE` stacking and `ALLOCATION()`,
`BASED(P)` with `ALLOCATE`/`SET`, pointer qualification `P -> X`,
`ADDR()`, `NULL()` (object-reference semantics, not byte overlay),
`DEFINED` (+`POSITION`) string overlays, `UNSPEC` as builtin and
pseudo-variable (32-bit two's-complement/IEEE-754/8-bit character
representations).

**Flow control** — `IF`/`THEN`/`ELSE`, all `DO` forms (groups, `WHILE`,
`UNTIL`, iterative with multiple specifications), `SELECT`/`WHEN`/
`OTHERWISE`, `GOTO` (incl. out of procedures and through LABEL
variables), `LEAVE`/`ITERATE`, `STOP`, **multiple closure** (a labeled
`END X;` closes every group opened since the group labeled `X`),
multiple assignment (`A, B, C = expr;` — right side evaluated once),
and `DISPLAY(expr) [REPLY(var)]` operator console I/O.
`STATIC` variables retain their values across invocations (INITIAL
applies once).  Condition prefixes `(NOSIZE):` and `(NOSTRINGRANGE):`
are honored: disabled SIZE truncates silently, disabled STRINGRANGE
clamps SUBSTR (SUBSCRIPTRANGE stays always-checked).  `ONLOC`,
`ONFILE` and `ONKEY` report the raising procedure / file / key inside
ON-units.  `ALLOCATE A(N);` re-specifies CONTROLLED bounds per
allocation.  `GET` supports the `COPY` option (echoes input) and
`GET STRING(...) EDIT(...)(...)`.

**F-personality features (v0.5.0)** — `(CHECK(A,B)):` prefixes monitor
assignments to the listed variables (default action prints `A=value;`
data-directed; `ON CHECK(x)` overrides).  Inside `ON CONVERSION` units
the pseudo-variables `ONSOURCE`/`ONCHAR` may be assigned to correct the
offending string, and a normal return **retries the conversion**.
Secondary entry points: `P2: ENTRY(...) RETURNS(...);` inside a
procedure.  `S1 = S2, BY NAME;` structure assignment by member name.
**Aggregate expressions**: elementwise array arithmetic and `||`
(`C = A + B;`, `C = A*2 + 1;`, `-A`) with scalar broadcast, and array
**cross-sections** `M(2,*)` / `M(*,1)` in expressions and as targets.
PRINT paging on SYSPRINT: `OPEN FILE(SYSPRINT) PAGESIZE(n);`,
`ENDPAGE` condition (the interrupted PUT resumes after the ON-unit),
`PUT PAGE`, and the `LINENO` / `COUNT` builtins.  `DCL X ENTRY(...)`
declarations are accepted as descriptive.

**ON-conditions** — `ON cond [SNAP] unit | SYSTEM`, `SIGNAL`, `REVERT`,
`ONCODE`/`ONCHAR`/`ONSOURCE`, user conditions via `CONDITION(name)`.
Raised: `ZERODIVIDE`, `FIXEDOVERFLOW`, `SIZE`, `CONVERSION`,
`SUBSCRIPTRANGE`, `STRINGRANGE`, `ENDFILE(f)`, `KEY(f)`,
`UNDEFINEDFILE(f)`, `ERROR`. Normal return from an ON-unit resumes
after the interrupted statement.

**Stream I/O** — `PUT`/`GET` `LIST`, `EDIT` (formats `A B F E X COL SKIP
P'...'`, remote `R(label)` with `FORMAT` statements, and repetition
factors: `(3) F(5)`, `2 (A(2), X(1))`, nested), `DATA` (data-directed,
both directions), `STRING`, `FILE(f)`, `SKIP(n)`, `PAGE`; SYSPRINT tab
stops.

**Record I/O** — `DCL f FILE RECORD [KEYED] ENV(INDEXED|REGIONAL(1))
[EXCLUSIVE]`, `OPEN` (`TITLE`, mode), `CLOSE`, `READ INTO [KEY|KEYTO]`,
`WRITE FROM [KEYFROM]`, `REWRITE`, `DELETE`, `UNLOCK`. CONSECUTIVE
files are text files (one record per line); INDEXED files persist as
sorted `key<TAB>record` lines; REGIONAL(1) uses numeric region keys.
Structures map to fixed-width record fields.  **LOCATE mode**:
`LOCATE recvar FILE(f) [SET(p)];` builds output records in based
buffers (written on the next operation), and `READ FILE(f) SET(p);`
delivers input records through the based variable declared on `p`.
`EXCLUSIVE` files lock records on keyed READ (released by
REWRITE/DELETE/UNLOCK).  The `EVENT(e)` option on
READ/WRITE/REWRITE/DELETE runs the operation asynchronously; its
conditions are raised at `WAIT(e)`.

**Object-graph extensions (v0.7.0)** — `REFER`: self-defining
structures, `2 ARR(N REFER(N)) FIXED` — the extent is taken from a
sibling member's current value (preferred) or an enclosing-scope
variable of the same name, and written back into the sibling so it
stays in sync. **Structure aggregate expressions**: elementwise
`S3 = S1 + S2;`, `S3 = S1 * 2;`, `-S1` across matching structure
shapes (comparisons and `& |` are not supported). **`ENTRY` variables**
hold a procedure as a first-class value — `DCL F ENTRY(FIXED)
RETURNS(FIXED); F = SOMEPROC; Y = F(5);` — assignment captures the
entry rather than calling it, `F(args)`/`CALL F(args)` calls through
it. (Passing a bare procedure name as an argument to an ENTRY-typed
*parameter* is not yet supported — assign it to an ENTRY variable
first.) `GENERIC` entry selection is not implemented. **`AREA`** /
**`OFFSET`** / **`EMPTY`**: `DCL A AREA(1000); DCL O OFFSET(A);
ALLOCATE recvar IN(A) SET(p);` — a logical allocation pool (this
interpreter has no byte-addressable storage, so AREA capacity/`AREA`
condition is leaf-count based, not byte-exact); `OFFSET` behaves as a
POINTER tied to an area; `EMPTY` is OFFSET's null, usable bare like
`NULL`.

**Separate compilation** — `python -m pli main.pli sub1.pli ...`
treats each file as a separately compiled external procedure; the one
with `OPTIONS(MAIN)` is the entry point, cross-file `CALL`s just work,
and `STATIC EXTERNAL` data is shared by name across all units
(`DCL X ENTRY ...` declarations are descriptive).  The preprocessor
supports **%PROCEDURE functions**: `%name: PROC(parms); ... RETURN(e);
%END;` — activated invocations `name(args)` in program text are
replaced by the returned value at compile time.

**Preprocessor** — `%DECLARE`, `%var = expr`, `%IF/%THEN/%ELSE`,
`%DO ... %END` (unrolled), `%INCLUDE`, `%ACTIVATE`/`%DEACTIVATE`,
`%GOTO` (forward) with `%label:`. Activated names are replaced by their
values in program text.

**Multitasking** — `CALL p(...) EVENT(E)` runs the procedure on a
thread; `WAIT(E1, E2 [, ...]) [(n)]`, `COMPLETION()`, `STATUS()`.

## DL/I

`CALL PLITDLI(count, call-code, pcb-ptr, io-area [, ssa...])` is
intercepted at the call site — no `PLITDLI` procedure needs to exist —
and executed against a single IRIS Global (default `^CUSTOMER`) acting
as a simplified hierarchical database. See `pli/examples/dli.pli`.

```
DCL PLITDLI ENTRY EXTERNAL;
CALL PLITDLI(count, call-code, pcb-ptr, io-area [, ssa-1 [, ssa-2 ...]]);
```

- `count` and `pcb-ptr` are accepted for source compatibility but
  ignored: this interpreter talks to exactly one database / one PCB.
- `io-area` receives the segment value on a successful `GU`/`GN`/`GNP`/
  `GHU`/`GHN`/`GHNP`, and supplies it on `ISRT`/`REPL`.
- Each SSA is a `CHARACTER` value: a bare segment name (`'CUSTOMER'`) is
  used as-is; a qualified SSA (`'CUSTOMER(CUSTNO=1001)'`) contributes
  the value side of its relational qualification as the subscript (the
  field name is documentation only — the Global is positional, not
  field-addressed).
- The status of the last call is read with the `DLISTATUS` builtin
  (`OK`, `GE`, `GB`, `GP`, `II`, `NOTHELD`, `NOLOCK`) since there is no
  PCB structure to inspect.

| Call | Meaning | Notes |
|------|---------|-------|
| GU   | Get Unique | positions by an explicit SSA path |
| GN   | Get Next | depth-first walk from the current position (or from the top if none) |
| GNP  | Get Next within Parent | first child of an explicit parent, or next sibling of the current position |
| GHU  | Get Hold Unique | like GU, plus takes a lock for a later DLET/REPL |
| GHN  | Get Hold Next | like GN, plus takes a lock |
| GHNP | Get Hold Next within Parent | like GNP, plus takes a lock |
| ISRT | Insert | fails with status II if the segment already exists |
| DLET | Delete | requires a prior GHU/GHN/GHNP on that segment |
| REPL | Replace | requires a prior GHU/GHN/GHNP on that segment |

Not implemented: multiple PCBs/databases, checkpoint/restart (`CHKP`/
`XRST`), rollback (`ROLB`/`ROLL`), and compound/Boolean SSA
qualification (each SSA carries at most one relational qualifier).

Configured via environment variables — all optional, with credentials
falling back to an interactive prompt:

| Variable | Default |
|---|---|
| `PLI_DLI_HOST` | `localhost` |
| `PLI_DLI_PORT` | `1972` |
| `PLI_DLI_NAMESPACE` | `SAMPLES` |
| `PLI_DLI_USERNAME` | *(prompted)* |
| `PLI_DLI_PASSWORD` | *(prompted)* |
| `PLI_DLI_GLOBAL` | `^CUSTOMER` |

Requires `intersystems-irispython` (see *Install* above), imported
lazily on the first `CALL PLITDLI` — no dependency for programs that
don't use it.

## Embedded SQL

`EXEC SQL ... ;` in the style of IBM's PL/I precompiler.  The SQL text
is captured opaquely at the lexer (quote-aware), so the PL/I grammar
never parses SQL.  Host variables `:NAME` and `:STRUCT.MEMBER` may
appear anywhere outside SQL string literals and are passed as bound
parameters; values fetched INTO host variables go through the normal
PL/I conversions (Db2 `DECIMAL` round-trips as exact `FIXED DECIMAL`).

Statements handled by the precompiler layer itself:

| Area | Statements |
| --- | --- |
| Connections | `CONNECT TO name`, `CONNECT RESET`, `SET CONNECTION name` (several connections, one current) |
| Singleton query | `SELECT ... INTO :v, ...` — `SQLCODE` +100 no row, −811 more than one |
| Cursors | `DECLARE c CURSOR [WITH HOLD] FOR sel`, `OPEN c`, `FETCH [FROM] c INTO :v,...` (+100 at end), `CLOSE c` |
| Transactions | `COMMIT [WORK]`, `ROLLBACK [WORK]`; commit on normal program end, rollback on abnormal end |
| Error handling | `WHENEVER SQLERROR\|SQLWARNING\|NOT FOUND  CONTINUE\|GOTO label\|STOP` |
| Compatibility | `INCLUDE SQLCA` (accepted no-op) |

Everything else — `INSERT`, `UPDATE`, `DELETE` (+100 when no rows hit),
`CREATE`/`DROP`/`ALTER`, `MERGE`, ... — passes through **verbatim**
after host-variable substitution, so the SQL dialect is whatever the
connected database accepts.  `SQLCODE`, `SQLSTATE` and `SQLERRM` are
set after every statement (0 ok / 100 not found / negative error).

Connections are defined in `pli_dbc.json`, searched next to the
program, then the current directory, then `~`:

```json
{ "SAMPLE": { "driver": "ibm_db",
              "url": "jdbc:db2://localhost:25000/sample",
              "user": "db2admin" },
  "TESTDB": { "driver": "sqlite", "url": "testdb.sqlite" },
  "LUWKRB": { "driver": "ibm_db",
              "url": "jdbc:db2://dbhost.example.com:50000/luwdb",
              "user": "svc-user", "securityMechanism": "11" } }
```

Drivers: `sqlite` (stdlib, works offline — see
`examples/include_fetch_demo/` for a self-contained sqlite-backed
demo) and `ibm_db` (`pip install ibm_db`; the `jdbc:db2://` URL is
translated to a native DSN, and on Windows the bundled Db2 clidriver
DLLs are put on the search path automatically).  A missing
`"password"` key prompts at CONNECT — masked in the terminal for the
CLI, a dialog in the IDE.  `examples/sqldemo_db2.pli` connects to the
`SAMPLE` entry (password prompt); `examples/sqldemo_kerberos.pli`
connects via Kerberos (optionally + SSL/JDBC, see below).

**`examples/sqldemo.pli` connects to `AZDO0D1O`, a real internal
ABS-schema Db2 instance over Kerberos+SSL** — it is *not* an offline
demo. **Before running it (or any example against `AZDO0D1O`/`SAMPLE`),
update the matching entry in your own `pli_dbc.json`** with a
connection, user, and (for `SAMPLE`) password you actually have access
to — the checked-in `pli_dbc.json` under `pli/examples/` is a real
config for this environment, deliberately committed *without* a
password (see the `"password"` note above), not a portable template.

Kerberos (typical for Db2 LUW): set `"securityMechanism": "11"` on an
`ibm_db` connection — no password field, no prompt; the DSN gets
`AUTHENTICATION=KERBEROS` and Windows SSPI (or a prior `kinit`) supplies
the ticket.

Kerberos **+ SSL together** (some Db2 LUW hosts mandate both) can't go
through `ibm_db`'s native CLI driver — it has no GSKit keystore for
SSL — so add `"ssl": true` alongside `"securityMechanism": "11"` to
route that connection through a JDBC driver instead
(`pip install jaydebeapi JPype1`; needs a JVM and an IBM Db2 JCC jar —
DbVisualizer's bundled jar or the IBM Data Server Driver's
`db2jcc4.jar`, auto-detected, or set `"jdbc_jar_path"` explicitly). A
JAAS login config is generated on the fly pointing at the Kerberos
ticket cache (Windows SSO or a prior `kinit`). Extra keys:
`"kerberosServerPrincipal"` (the DB2 server's principal, e.g.
`"db2agl1/host@SERVER.REALM"`) and `"realm"` — **your own** Kerberos
realm (e.g. `"ALLIANZDE.ROOTDOM.NET"`), not the server's realm, which
only belongs in `kerberosServerPrincipal`; getting this backwards
causes a `GSSException` even with a valid ticket.

Not implemented (yet): NULL indicator variables (fetching NULL sets
`SQLCODE` −305), dynamic SQL (`PREPARE`/`EXECUTE`/`EXECUTE IMMEDIATE`),
positioned `UPDATE/DELETE ... WHERE CURRENT OF` (cursors are
client-side), stored-procedure OUT parameters, scrollable cursors.
SQL errors surface at run time via SQLCODE, not at compile time.

### %INCLUDE fallback (DB2 source repository)

`%INCLUDE name;` normally reads `name.pli` next to the source. If that
file doesn't exist, and `pli_dbc.json` has an optional
`"_source_repository"` block, the preprocessor falls back to fetching
the member's source from a DB2 table instead — for legacy PL/I
copybooks that live as CLOB rows in a mainframe-migrated source
repository rather than as files on disk:

```json
{ "AZDO0D1O": { "driver": "ibm_db", "url": "jdbc:db2://..." },
  "_source_repository": {
    "connection": "AZDO0D1O",
    "table": "ABS.CCM_SOURCE_REPO",
    "name_column": "CCMOBJT",
    "type_column": "CCMTYPT",
    "source_column": "QUELLE",
    "newline_char": "",
    "cache_dir": ".pli_include_cache"
  } }
```

`"connection"` reuses an existing entry in the same file (its driver,
Kerberos, SSL and JDBC settings all apply). A fetched member is cached
under `cache_dir` (default `.pli_include_cache`, relative to wherever
`pli_dbc.json` was found) and never re-fetched once cached — the
filesystem itself is the "already extracted" tracking, so repeated
runs and diamond includes (a member pulled in by two different
parents) just become ordinary local-file hits. Since a cached member
is a real `.pli` file, any `%INCLUDE` inside *it* recurses through the
normal local-file path automatically — no separate "recursive fetch"
step is needed. A fetch failure (bad config, connection error, member
not found in the table) always surfaces as a normal `PreprocError`,
combined with the original "file not found" message, never a silent
skip. `%INCLUDE` also now caps nesting at 20 levels, raising a clean
error for a circular `%INCLUDE` instead of a raw `RecursionError`.
`examples/include_fetch_demo/` is a self-contained, offline-runnable
demo using the `sqlite` driver.

**Operators & builtins** — full operator set incl. `¬`/`^`/`~` spellings
and bit-string logic; ~75 builtins:

- *string*: `SUBSTR LENGTH INDEX VERIFY VERIFYR SEARCH SEARCHR TALLY
  TRANSLATE REPEAT COPY TRIM LEFT RIGHT CENTER/CENTRE REVERSE HIGH LOW
  BOOL STRING LOWERCASE UPPERCASE CHAR BIT` (INDEX/VERIFY/SEARCH take
  an optional start position; TRIM takes optional character sets)
- *arithmetic*: `ABS MOD REM MIN MAX SIGN CEIL FLOOR TRUNC ROUND
  ADD SUBTRACT MULTIPLY DIVIDE` (`(x,y,p[,q])` forms with exact decimal
  results), `FIXED FLOAT BINARY DECIMAL`
- *math*: `SQRT EXP LOG LOG2 LOG10 SIN COS TAN ASIN ACOS ATAN(y[,x])
  ATAND SIND COSD TAND SINH COSH TANH ATANH ERF ERFC RANDOM`
- *array*: `HBOUND LBOUND DIM SUM PROD`
- *complex*: `REAL IMAG CONJG COMPLEX`
- *storage*: `NULL EMPTY ADDR ALLOCATION UNSPEC`
- *conditions/tasking/system*: `ONCODE ONCHAR ONSOURCE COMPLETION
  STATUS DATE TIME DATETIME`

plus pseudo-variables `SUBSTR` and `UNSPEC`.

## Known deviations from PL/I(F)

- Statement keywords are reserved (real PL/I has no reserved words).
- BASED/pointer storage uses object references, not byte-addressable
  storage; `P->X` reinterprets the pointed-to object, not raw bytes.
- Integer/integer division is not scale-preserving (see above).
- iSUB defining, `DO REPEAT`, REGIONAL(2)/REGIONAL(3), `GENERIC` entry
  selection, and `%GOTO` backward jumps are not implemented.
- ON-unit resumption is at statement granularity; PUT LIST tab stops
  are fixed at 24 columns.

## Java transpiler (experimental)

```
python scripts/build_java.py program.pli [-o outdir] [--run] [--diff]
```

Translates a structured PL/I subset into real, standalone Java source
(`pli/javagen.py`, runtime `javart/PLI.java`) — not an interpreter or
a serialized AST, an actual `.java` file you can `javac`/`java` with no
Python involved at run time. `--run` compiles and executes it; `--diff`
also runs `python -m pli` on the same program and diffs stdout (line
endings normalized — Python's stdout is `\r\n` on Windows, Java's
`PrintStream` never is, which is a platform artifact, not a behavioral
difference), which is how the backend is validated (`pli/examples/
hello.pli`, `fibfact.pli` (recursion + nested-procedure closures),
`sort.pli` (arrays, by-reference CALL, the GOTO dispatch loop below),
`strings.pli` (bit-string logic, SUBSTR pseudo-variable), `average.pli`
(GET LIST), `match_demo.pli` (multiple top-level external procedures,
one a self-recursive `CHAR(*)`-parameter function), `javaext.pli`
(multi-dimensional arrays, COMPLEX, multitasking, PUT/GET DATA/STRING,
FORMAT + R()), `structest.pli`/`structest2.pli` (nested structures,
LIKE, qualified/unqualified/partially-qualified member access,
structure assignment, structure and structure-member CALL parameters),
and `recio.pli` (CONSECUTIVE + INDEXED record I/O) all match
byte-for-byte; `javaext.pli` needs `--stdin "ROW=5 COL=5;"` since it
exercises `GET DATA`).

Design: every PL/I scalar is generated as a 1-element Java array
(`long[1]`/`double[1]`/`String[1]`/`PLI.Complex[1]`/`PLI.Event[1]`) so
CALL-by-reference is just array aliasing — the same trick the
interpreter's `Variable` box uses. PL/I arrays map to a single flat
Java array regardless of dimensionality, row-major, with bounds-checked
subscripting (`PLI.idx`/`PLI.idx1`, raising `SUBSCRIPTRANGE`). Nested
PL/I procedures become non-static Java inner classes, relying on
Java's automatic implicit-outer-instance capture to reproduce PL/I's
lexical scoping — recursive self-calls and calls to sibling/enclosing
procedures need zero special-casing at the call site, and a codegen
symbol-table chain (`Ctx.lookup`, parent-linked to the enclosing
procedure's `Ctx`) lets a nested procedure's code reference a variable
declared in an *enclosing* procedure (e.g. a shared accumulator a
worker task updates) — Java's own scoping resolves the actual field
access with no qualification needed. `GOTO` (restricted to labels at a
procedure's own top level — see below) compiles via the classic
goto-elimination technique: the label set becomes `case N:` blocks in
a `switch` inside a `while(true)` dispatch loop, with a normal label
just falling through to the next case and a `GOTO` becoming
`pc = N; continue dispatch;`.

Scope: procedures (external, nested, recursive), FIXED BINARY (→ Java
`long`) / FLOAT (→ `double`) / CHAR / BIT / COMPLEX scalars, arrays of
any dimensionality with arbitrary (including non-1) bounds, aggregate
whole-array assignment (`A = B;` element-wise copy, `A = 0;` broadcast
— evaluated once even for a side-effecting RHS, then applied in a
loop), `IF`/`DO`(`WHILE`|`UNTIL`|iterative)/`SELECT`, `LEAVE`/`ITERATE`
(→ labeled `break`/`continue`), `CALL`/`RETURN`, multitasking
(`CALL p(...) EVENT(e)` spawns a Java thread via `PLI.spawnTask`;
`WAIT`/`COMPLETION`/`STATUS` — the same unsynchronized-shared-state
hazard as real PL/I multitasking and as the Python interpreter's
thread-based implementation applies here too, faithfully, not a new
deviation), `PUT`/`GET LIST`, `PUT`/`GET DATA` (explicit ref list
required — no bare `PUT DATA;`/`GET DATA;` dump-everything form; GET
DATA is order-independent name=value matching, not positional), `PUT`/
`GET STRING`, `PUT EDIT`/`GET`-less-`EDIT` format items `A`, `F`, `X`,
`COL`, plus `FORMAT` statements and the `R(label)` remote-format
reference (with `(n)FMT` repetition groups), and the string/bit/
arithmetic/complex builtins (`SUBSTR LENGTH INDEX TRIM UPPERCASE
LOWERCASE TRANSLATE VERIFY REPEAT COPY ABS MOD MIN MAX SQRT TRUNC CEIL
FLOOR ROUND HBOUND LBOUND DIM REAL IMAG CONJG COMPLEX COMPLETION
STATUS`, `HBOUND`/`LBOUND`/`DIM` all take the optional dimension-number
argument). The `%` preprocessor runs for free (it's already a
text-in/text-out pass before parsing).

Structures: `DCL 1 X, 2 ...;`, `LIKE`, nested structures, qualified
(`EMP.NAME.FIRST`), partially-qualified (`EMP.LAST`), and unqualified
(`SALARY`, if unique in scope) member access, structure assignment
(leaf-by-leaf, matching shape required; scalar broadcast requires
every leaf to share one Java type), structure and structure-member
CALL arguments (by reference), and `PUT LIST`/`PUT DATA` flattening a
whole structure to its leaves. Every distinct *shape* — not
declaration site — gets one generated Java class, so a structure
parameter re-declared in a callee shares the caller's class instead of
an incompatible new one. **Not yet supported**: arrays of structures,
`BASED`/`CONTROLLED` structures, `REFER`, structure aggregate
expressions (`S3 = S1 + S2;`), `BY NAME` assignment, and whole-array
or whole-structure references in a `GET DATA` list (scalars only
there).

Record I/O: `DCL f FILE RECORD [KEYED] [ENV(INDEXED)];`, `OPEN`
(`INPUT`/`OUTPUT`/`UPDATE`, `TITLE(...)`), `CLOSE`, `READ INTO(...)`
(`KEY(...)` for a keyed lookup, bare for sequential with optional
`KEYTO(...)`), `WRITE FROM(...)` (`KEYFROM(...)` on an INDEXED file),
`REWRITE FROM(...) KEY(...)`, `DELETE KEY(...)` — CONSECUTIVE files are
one record per line; INDEXED files are an in-memory sorted string-keyed
map persisted as `key\trecord` lines on `CLOSE`. Fixed-width field
encoding matches the interpreter's `_leaf_width`/`_record_from`/
`_record_into` exactly (FIXED right-justified width 12, CHAR/BIT
left-justified to their declared length or a 24/8-char default,
FLOAT right-justified width 24). **Not supported**: `LOCATE` mode,
`EXCLUSIVE` files, `ENV(REGIONAL(...))`, `EVENT(...)` on record I/O,
`UNLOCK`, `SET(...)` (BASED-variable READ) — all raise a clear
`CodegenError` naming the construct.

**Not implemented** (raises a clear `CodegenError` naming the
construct and line, rather than silently mistranslating): PICTURE,
ON-conditions (so record I/O's `ENDFILE`/`KEY` conditions terminate the
program rather than being trappable — bound loops by a known record
count instead, as `recio.pli` does), BASED/POINTER/CONTROLLED/DEFINED/
UNSPEC, exact FIXED DECIMAL (decimal literals are approximated as IEEE
double — no `FixedDec`/`BigDecimal` in this backend), `BEGIN` blocks,
`GET EDIT`, assumed-size `(*)` bounds within a multi-dimensional
array (only a whole 1-D assumed-size parameter, as before), array
elements in a `PUT`/`GET DATA` list on the GET side (PUT DATA supports
them; GET DATA is scalars only), and labels or DECLAREs nested inside a
DO/IF/SELECT/BEGIN body (only labels at a procedure's own top level are
reachable by GOTO). SQL is not ported to this backend (already works
in the Python interpreter).

## Building standalone executables

```
pip install pyinstaller
```

**Compile one PL/I program into its own executable** — the main
event: `stage6.pli` in, a standalone `stage6.exe` / `stage6` out that
runs with **no arguments**, no Python, and no dependency on the
original source file or its directory:

```
python scripts/build.py pli\examples\stage6.pli
dist\stage6.exe
```

The `%` compile-time preprocessor (`%INCLUDE`, `%DO`, `%PROC`, ...) is
fully expanded once at build time and the resulting source is
embedded directly in the executable — `%INCLUDE` members do not need
to travel with the binary. Several files compile together as one
separately-compiled program, exactly like `python -m pli` does:

```
python scripts/build.py pli\examples\stage9.pli pli\examples\stage9sub.pli -o stage9
dist\stage9.exe
```

`-o name` sets the output name (default: the first source file's
basename). The compiled program still reads real `SYSIN`/`GET` input
normally when you actually run it (`echo 1 2 3 | dist\avg.exe`) — only
the *build script's own* smoke test feeds it empty input, to catch
build failures without hanging on programs that expect real data.

**Build the generic, reusable interpreter instead** — `pli.exe`, run
as `pli.exe program.pli` against *any* program afterwards (this is
what `pip install`-free users download from the
[Releases page](../../releases)):

```
python scripts/build.py
```

Produces a native `pli`/`pli.exe` for **whatever OS you run it on**
(no cross-compilation — build on Windows for a Windows binary, on
Linux for a Linux binary), packaged with the examples and docs into
`dist/pli-<version>-<platform>-<arch>[.zip]`. `.github/workflows/
release.yml` runs this mode on both Windows and Linux for every pushed
`vX.Y.Z` tag and attaches both zips to the GitHub Release.

Both modes: CLI only, the Tkinter IDE is not packaged this way; the
frozen binary bundles sqlite for `EXEC SQL` but not the Db2 driver
(`ibm_db` pulls in a native client library with its own redistribution
terms) — Db2 support needs the source install (`pip install ibm_db`).

**`pli-build`** (`pli/build.py`, run via `bin/pli-build` / `./pli-build.sh`
/ `pli-build.bat` or `python -m pli.build`) does the same two jobs as
`scripts/build.py`
above, but as a shipped, always-available tool rather than a dev-only
script — it's also what the IDE's **Build EXE...** button (see *The
IDE* below) calls:

```
bin/pli-build pli/examples/stage6.pli -o stage6      # bake one program in
bin/pli-build --interpreter -o pli                   # generic frozen interpreter
```

Excludes the Db2 driver stack by default same as `scripts/build.py`
(`--with-db2` opts back in). **Key difference:** `pli-build` bundles
the raw `.pli` source file(s) via PyInstaller `--add-data` and runs the
`%` preprocessor at run time inside the frozen exe, rather than
expanding it once at build time — a program that `%INCLUDE`s a sibling
member needs that member bundled alongside it (or fetched via the
DB2 `%INCLUDE` fallback below) to work standalone, unlike a
`scripts/build.py` binary which has `%INCLUDE` fully baked in.
`bin/pli-build-selftest` checks both modes against `hello.pli`.

On Linux, a `.pli` file can also be marked directly executable — see
`pli/examples/shebang_demo.pli`:
```
#!/usr/bin/env pli
SHEBANGDEMO: PROCEDURE OPTIONS(MAIN);
   PUT LIST('shebang line was skipped, this still runs');
END SHEBANGDEMO;
```
```
chmod +x pli/examples/shebang_demo.pli && ./pli/examples/shebang_demo.pli
```

## Layout

| File | Role |
| --- | --- |
| `pli/lexer.py` | `ply.lex` tokenizer |
| `pli/parser.py` | `ply.yacc` LALR(1) grammar → AST, multi-error recovery |
| `pli/nodes.py` | AST node classes |
| `pli/interpreter.py` | evaluator, runtime types, conditions, I/O, builtins |
| `pli/picture.py` | PICTURE parsing/editing |
| `pli/fixeddec.py` | exact FIXED DECIMAL(p,q) arithmetic |
| `pli/preproc.py` | compile-time preprocessor |
| `pli/sql.py` | EXEC SQL runtime (connections, cursors, WHENEVER) |
| `pli/dli_preproc.py` | DL/I bridge: `CALL PLITDLI(...)` against an IRIS Global |
| `pli/javagen.py` | experimental PL/I → Java transpiler (AST → Java source) |
| `pli/__main__.py` | CLI entry point (`python -m pli`) |
| `pli/examples/` | demo programs |
| `javart/PLI.java` | runtime library for transpiled Java (I/O, builtins, conditions) |
| `scripts/build_java.py` | drives the Java transpiler: parse → codegen → javac [→ run/diff] |
| `pli_ide.py` | Tkinter IDE (edit / compile / run) |
| `pli.bat`, `pli-ide.bat` | Windows launchers |
| `bin/pli`, `bin/pli-ide` | Unix (Linux/macOS) launchers (`PATH`-friendly) |
| `pli.sh`, `pli-ide.sh` | same Unix launchers, root-level (no `PATH` setup needed) |
| `scripts/build.py` | builds a standalone CLI executable (PyInstaller) |
| `scripts/pli_cli_entry.py` | PyInstaller entry point for the CLI |
| `pli/build.py` | `pli-build` tool: freeze a program (or the interpreter) via PyInstaller at run time |
| `bin/pli-build`, `pli-build.sh`, `pli-build.bat` | launchers for `pli-build` |
| `bin/pli-build-selftest` | checks `pli-build`'s two modes against `hello.pli` |
| `pli/include_fetch.py` | `%INCLUDE` DB2 source-repository fallback fetch/cache |
| `.github/workflows/release.yml` | CI: builds + releases Windows/Linux binaries per tag |
