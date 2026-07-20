# pli — a PL/I(F)-level interpreter in Python (built on PLY)

A tree-walking interpreter for a large subset of IBM PL/I at roughly the
F-compiler language level, using [PLY](https://www.dabeaz.com/ply/)
(`ply.lex` / `ply.yacc`) for the lexer and LALR(1) grammar — plus a small
stand-alone Tkinter IDE.

## Requirements

Python 3.10+ and:

```
pip install -r requirements.txt      # just ply
```

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
for plain `pli myprog.pli`):

```
bin/pli myprog.pli
bin/pli-ide myprog.pli
```

Programmatic API:

```python
from pli import run_source, run_file
run_source("H: PROC OPTIONS(MAIN); PUT LIST('HI'); END H;")
```

Example programs live in `pli/examples/` — `stage1.pli` … `stage5.pli`
exercise the F-level features; the rest are basics.

## The IDE

```
python pli_ide.py [program.pli]      # or double-click pli-ide.bat
```

A stand-alone Tkinter IDE (stdlib only): editor with line numbers,
PL/I syntax highlighting and find/replace (Ctrl+F, F3); **Compile**
(F7) lists *all* syntax errors at once (panic-mode recovery) with
click-to-jump line highlighting; **Run** (F5) executes on a worker
thread with live SYSPRINT output, a pre-supplied SYSIN tab, and an
interactive `SYSIN>` console for `GET` input (`/*` or the EOF button
signals ENDFILE). The interpreter itself has no dependency on the IDE.

## Supported language

**Program structure** — main procedure with `OPTIONS(MAIN)`, nested
procedures (recursive, parameters, `RETURNS`), `BEGIN` blocks, labels,
condition prefixes `(SIZE):`.

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
  "TESTDB": { "driver": "sqlite", "url": "testdb.sqlite" } }
```

Drivers: `sqlite` (stdlib — used by `examples/sqldemo.pli`, works
offline) and `ibm_db` (`pip install ibm_db`; the `jdbc:db2://` URL is
translated to a native DSN, and on Windows the bundled Db2 clidriver
DLLs are put on the search path automatically).  A missing
`"password"` key prompts at CONNECT — masked in the terminal for the
CLI, a dialog in the IDE.  `examples/sqldemo_db2.pli` is the same demo
against a real Db2.

Not implemented (yet): NULL indicator variables (fetching NULL sets
`SQLCODE` −305), dynamic SQL (`PREPARE`/`EXECUTE`/`EXECUTE IMMEDIATE`),
positioned `UPDATE/DELETE ... WHERE CURRENT OF` (cursors are
client-side), stored-procedure OUT parameters, scrollable cursors.
SQL errors surface at run time via SQLCODE, not at compile time.

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
- *storage*: `NULL ADDR ALLOCATION UNSPEC`
- *conditions/tasking/system*: `ONCODE ONCHAR ONSOURCE COMPLETION
  STATUS DATE TIME DATETIME`

plus pseudo-variables `SUBSTR` and `UNSPEC`.

## Known deviations from PL/I(F)

- Statement keywords are reserved (real PL/I has no reserved words).
- BASED/pointer storage uses object references, not byte-addressable
  storage; `P->X` reinterprets the pointed-to object, not raw bytes.
- Integer/integer division is not scale-preserving (see above).
- `BY NAME` assignment, iSUB defining, AREA/OFFSET, `DO REPEAT`,
  REGIONAL files, GENERIC entries, array cross-sections (`A(*,2)`),
  and `%GOTO` backward jumps are not implemented.
- ON-unit resumption is at statement granularity; PUT LIST tab stops
  are fixed at 24 columns.

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
| `pli/__main__.py` | CLI entry point (`python -m pli`) |
| `pli/examples/` | demo programs |
| `pli_ide.py` | Tkinter IDE (edit / compile / run) |
| `pli.bat`, `pli-ide.bat` | Windows launchers |
| `bin/pli`, `bin/pli-ide` | Unix (Linux/macOS) launchers |
