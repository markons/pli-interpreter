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
structure arguments. Implicit declaration follows the I–N rule.

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
variables), `LEAVE`/`ITERATE`, `STOP`.

**ON-conditions** — `ON cond [SNAP] unit | SYSTEM`, `SIGNAL`, `REVERT`,
`ONCODE`/`ONCHAR`/`ONSOURCE`, user conditions via `CONDITION(name)`.
Raised: `ZERODIVIDE`, `FIXEDOVERFLOW`, `SIZE`, `CONVERSION`,
`SUBSCRIPTRANGE`, `STRINGRANGE`, `ENDFILE(f)`, `KEY(f)`,
`UNDEFINEDFILE(f)`, `ERROR`. Normal return from an ON-unit resumes
after the interrupted statement.

**Stream I/O** — `PUT`/`GET` `LIST`, `EDIT` (formats `A B F E X COL SKIP
P'...'` and remote `R(label)` with `FORMAT` statements), `DATA`
(data-directed, both directions), `STRING`, `FILE(f)`, `SKIP(n)`,
`PAGE`; SYSPRINT tab stops.

**Record I/O** — `DCL f FILE RECORD [KEYED] ENV(INDEXED)`, `OPEN`
(`TITLE`, mode), `CLOSE`, `READ INTO [KEY|KEYTO]`, `WRITE FROM
[KEYFROM]`, `REWRITE`, `DELETE`. CONSECUTIVE files are text files (one
record per line); INDEXED files persist as sorted `key<TAB>record`
lines. Structures map to fixed-width record fields.

**Preprocessor** — `%DECLARE`, `%var = expr`, `%IF/%THEN/%ELSE`,
`%DO ... %END` (unrolled), `%INCLUDE`, `%ACTIVATE`/`%DEACTIVATE`,
`%GOTO` (forward) with `%label:`. Activated names are replaced by their
values in program text.

**Multitasking** — `CALL p(...) EVENT(E)` runs the procedure on a
thread; `WAIT(E1, E2 [, ...]) [(n)]`, `COMPLETION()`, `STATUS()`.

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
- Arrays of structures, `BY NAME` assignment, iSUB defining, AREA/
  OFFSET, `DO REPEAT`, REGIONAL files, GENERIC entries, and `%GOTO`
  backward jumps are not implemented.
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
| `pli/__main__.py` | CLI entry point (`python -m pli`) |
| `pli/examples/` | demo programs |
| `pli_ide.py` | Tkinter IDE (edit / compile / run) |
| `pli.bat`, `pli-ide.bat` | Windows launchers |
| `bin/pli`, `bin/pli-ide` | Unix (Linux/macOS) launchers |
