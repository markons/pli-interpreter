# Formal syntax of the pli interpreter (v0.3.0)

This is the grammar actually implemented by `pli/lexer.py` and
`pli/parser.py` (the nonterminal names below match the yacc rules), in
BNF.

**Notation**

```
<name>        nonterminal
::=           "is defined as"
|             alternative
[ x ]         optional (zero or one)
{ x }         repetition (zero or more)
'TEXT'        terminal keyword / punctuation (case-insensitive in source)
UPPERCASE     lexical token class (defined in section 1)
(* ... *)     comment
```

---

## 0. Translation phases

Source text passes through, in order:

1. **Compile-time preprocessor** — executes `%` statements and
   substitutes activated identifiers (section 8).
2. **Lexer** — tokenizes; `EXEC SQL ... ;` is captured whole as one
   opaque token (section 1).
3. **Multiple-closure filter** — a labeled `'END' ID ';'` closes every
   group (`DO`/`BEGIN`/`SELECT`/`PROCEDURE`) opened since the group
   labeled ID; the filter injects the omitted `'END' ';'` tokens.
   (This transformation is outside BNF by nature.)
4. **LALR(1) parser** — the grammar of sections 2–7.  On a syntax
   error, `<stmt> ::= error ';'` recovery resumes at the next
   semicolon so all errors of a compilation are reported.

---

## 1. Lexical elements

```
ID          ::= letter { letter | digit }
letter      ::= 'A'..'Z' | 'a'..'z' | '_' | '$' | '#' | '@'
digit       ::= '0'..'9'

NUMBER      ::= digits [ '.' [digits] ] [ exponent ]      (* '.' => exact FIXED DECIMAL *)
              | '.' digits [ exponent ]
              | digits 'B'                                (* binary constant: 101B     *)
              | digits [ '.' digits ] 'I'                 (* imaginary constant: 4I    *)
exponent    ::= 'E' [ '+' | '-' ] digits                  (* => FLOAT                  *)
digits      ::= digit { digit }

STRING      ::= '''' { any-char | '''''' } ''''           (* '' inside = literal quote *)
BITSTRING   ::= STRING 'B'                                (* body restricted to 0|1    *)

EXECSQL     ::= 'EXEC' 'SQL' sql-text ';'                 (* sql-text is opaque; a ';'
                                                             inside '...' does not end it *)

comment     ::= '/*' any-text '*/'                        (* may span lines, ignored  *)

not-sign    ::= '¬' | '^' | '~'                           (* interchangeable          *)
operators   ::= '**' '||' '<=' '>=' '<' '>' '=' not-sign'=' '<>'
              | not-sign'<' not-sign'>' '+' '-' '*' '/' '&' '|' not-sign
              | '->' '(' ')' ',' ';' ':' '.'
```

Identifiers and keywords are case-insensitive.  Unlike real PL/I, the
statement keywords are **reserved** (`IF DO END DECLARE PUT GET ...`;
full list = `reserved` table in `pli/lexer.py`).

---

## 2. Program structure

```
<program>       ::= <stmt_list>
                    (* must contain a labeled PROCEDURE; the one with
                       OPTIONS(MAIN) — or the first — is the entry *)

<stmt_list>     ::= { <stmt> }

<stmt>          ::= ID ':' <stmt>                          (* label *)
                  | '(' <id_list> ')' ':' <stmt>           (* condition prefix *)
                  | <simple_stmt>

<simple_stmt>   ::= <proc_stmt>    | <begin_stmt>  | <decl_stmt>
                  | <assign_stmt>  | <call_stmt>   | <if_stmt>
                  | <do_stmt>      | <select_stmt> | <goto_stmt>
                  | <return_stmt>  | <stop_stmt>   | <null_stmt>
                  | <leave_stmt>   | <iterate_stmt>
                  | <on_stmt>      | <signal_stmt> | <revert_stmt>
                  | <put_stmt>     | <get_stmt>    | <format_stmt>
                  | <io_stmt>      | <allocate_stmt> | <free_stmt>
                  | <wait_stmt>    | <execsql_stmt>

<proc_stmt>     ::= ( 'PROCEDURE' | 'PROC' ) { <proc_attr> } ';'
                        <stmt_list>
                    'END' [ ID ] ';'
<proc_attr>     ::= '(' <id_list> ')'                      (* parameters *)
                  | 'OPTIONS' '(' <id_list> ')'            (* e.g. MAIN  *)
                  | 'RETURNS' '(' <attr_seq> ')'
                  | ID                                     (* RECURSIVE  *)

<begin_stmt>    ::= 'BEGIN' ';' <stmt_list> 'END' [ ID ] ';'

<null_stmt>     ::= ';'
<id_list>       ::= ID { ',' ID }
```

---

## 3. Declarations

```
<decl_stmt>     ::= ( 'DECLARE' | 'DCL' ) <decl_list> ';'
<decl_list>     ::= <decl_item> { ',' <decl_item> }

<decl_item>     ::= ID [ '(' <bound_list> ')' ] <attr_seq>
                  | '(' <id_list> ')' <attr_seq>           (* factored  *)
                  | NUMBER ID [ '(' <bound_list> ')' ] <attr_seq>
                                                           (* structure level;
                                                              level 1 may carry
                                                              dimensions =>
                                                              array of structures *)

<bound_list>    ::= <bound> { ',' <bound> }
<bound>         ::= <expr> | <expr> ':' <expr> | '*'       (* '*' = parameter *)

<attr_seq>      ::= { <attr> }
<attr>          ::= 'FIXED'   [ <precision> ]
                  | 'FLOAT'   [ <precision> ]
                  | ( 'BINARY' | 'BIN' )   [ <precision> ]
                  | ( 'DECIMAL' | 'DEC' )  [ <precision> ]
                  | ( 'CHARACTER' | 'CHAR' ) [ '(' <length> ')' ]
                  | 'BIT' [ '(' <length> ')' ]
                  | 'VARYING' | 'STATIC' | 'AUTOMATIC' | 'LABEL'
                  | ( 'INITIAL' | 'INIT' ) '(' <init_list> ')'
                  | 'LIKE' <ref>
                  | ( 'PICTURE' | 'PIC' ) STRING
                  | 'BASED' [ '(' <ref> ')' ]
                  | ( 'CONTROLLED' | 'CTL' )
                  | ( 'DEFINED' | 'DEF' ) ( <ref> | '(' <ref> ')' )
                  | 'FILE'
                  | ID [ '(' <expr_list> ')' ]
                    (* known attribute identifiers only: POINTER PTR
                       POSITION BUILTIN EXTERNAL INTERNAL ALIGNED
                       UNALIGNED REAL COMPLEX ABNORMAL NORMAL EVENT
                       TASK STREAM RECORD INPUT OUTPUT UPDATE KEYED
                       PRINT TITLE ENVIRONMENT ENV SEQUENTIAL DIRECT
                       BUFFERED UNBUFFERED — anything else is a
                       compile-time error *)

<precision>     ::= '(' NUMBER [ ',' NUMBER ] ')'
<length>        ::= <expr> | '*'

<init_list>     ::= <init_item> { ',' <init_item> }
<init_item>     ::= <expr>
                  | '(' <expr> ')' <init_val>              (* iteration factor *)
                  | '(' <expr> ')' '*'                     (* skip n elements  *)
<init_val>      ::= NUMBER | '+' NUMBER | '-' NUMBER | STRING | BITSTRING
```

---

## 4. Executable statements

```
<assign_stmt>   ::= <ref> '=' <expr> ';'
                    (* <ref> may be a pseudo-variable: SUBSTR(...) or
                       UNSPEC(...) as target *)

<ref>           ::= ID [ '(' <expr_list> ')' ]
                  | <ref> '.'  ID [ '(' <expr_list> ')' ]  (* member    *)
                  | <ref> '->' ID [ '(' <expr_list> ')' ]  (* pointer   *)

<call_stmt>     ::= 'CALL' ID [ '(' [ <expr_list> ] ')' ] { <call_opt> } ';'
<call_opt>      ::= ID '(' <expr> ')'          (* TASK | EVENT | PRIORITY *)

<if_stmt>       ::= 'IF' <expr> 'THEN' <stmt> [ 'ELSE' <stmt> ]

<do_stmt>       ::= 'DO' ';' <stmt_list> 'END' [ ID ] ';'
                  | 'DO' <cond> { <cond> } ';' <stmt_list> 'END' [ ID ] ';'
                  | 'DO' ID '=' <do_spec> { ',' <do_spec> } ';'
                        <stmt_list> 'END' [ ID ] ';'
<cond>          ::= 'WHILE' '(' <expr> ')' | 'UNTIL' '(' <expr> ')'
<do_spec>       ::= <expr> { <do_ctl> }
<do_ctl>        ::= 'TO' <expr> | 'BY' <expr> | <cond>

<select_stmt>   ::= 'SELECT' [ '(' <expr> ')' ] ';'
                        { 'WHEN' '(' <expr_list> ')' <stmt> }
                        [ ( 'OTHERWISE' | 'OTHER' ) <stmt> ]
                    'END' [ ID ] ';'

<goto_stmt>     ::= 'GOTO' ID ';' | 'GO' 'TO' ID ';'
<return_stmt>   ::= 'RETURN' [ <expr> ] ';'
<stop_stmt>     ::= 'STOP' ';'
<leave_stmt>    ::= 'LEAVE'   [ ID ] ';'
<iterate_stmt>  ::= 'ITERATE' [ ID ] ';'

<on_stmt>       ::= 'ON' <cond_name> [ 'SNAP' ] ( <stmt> | 'SYSTEM' ';' )
<signal_stmt>   ::= 'SIGNAL' <cond_name> ';'
<revert_stmt>   ::= 'REVERT' <cond_name> ';'
<cond_name>     ::= ID [ '(' ID ')' ]      (* e.g. ENDFILE(SYSIN),
                                              CONDITION(name) *)

<allocate_stmt> ::= ( 'ALLOCATE' | 'ALLOC' ) <alloc_item>
                        { ',' <alloc_item> } ';'
<alloc_item>    ::= ID [ 'SET' '(' <ref> ')' ]
<free_stmt>     ::= 'FREE' <id_list> ';'

<wait_stmt>     ::= 'WAIT' '(' <ref_list> ')' [ '(' <expr> ')' ] ';'

<execsql_stmt>  ::= EXECSQL                    (* see section 7 *)
```

---

## 5. Input / output

```
<put_stmt>      ::= 'PUT' { <put_clause> } ';'
<put_clause>    ::= 'PAGE'
                  | 'SKIP' [ '(' <expr> ')' ]
                  | 'LIST' '(' <expr_list> ')'
                  | 'EDIT' '(' <expr_list> ')' '(' <format_list> ')'
                  | 'DATA' [ '(' <ref_list> ')' ]
                  | 'STRING' '(' <ref> ')'
                  | 'FILE' '(' ID ')'

<get_stmt>      ::= 'GET' { <get_clause> } ';'
<get_clause>    ::= 'SKIP' [ '(' <expr> ')' ]
                  | 'LIST' '(' <ref_list> ')'
                  | 'EDIT' '(' <ref_list> ')' '(' <format_list> ')'
                  | 'DATA' [ '(' <ref_list> ')' ]
                  | 'STRING' '(' <expr> ')'
                  | 'FILE' '(' ID ')'

<format_list>   ::= <format_item> { ',' <format_item> }
<format_item>   ::= ID [ '(' <expr_list> ')' ]     (* A B F E X COL(UMN) R *)
                  | 'SKIP' [ '(' <expr> ')' ]
                  | ID STRING                      (* P'picture'           *)
                  | NUMBER <format_item>           (* repetition           *)
                  | NUMBER '(' <format_list> ')'   (* repeated group       *)
                  | '(' <expr> ')' <format_item>
                  | '(' <expr> ')' '(' <format_list> ')'

<format_stmt>   ::= 'FORMAT' '(' <format_list> ')' ';'
                    (* must be labeled; referenced by R(label) *)

<io_stmt>       ::= <io_verb> { <io_opt> } ';'
<io_verb>       ::= 'OPEN' | 'CLOSE' | 'READ' | 'WRITE'
                  | 'REWRITE' | 'DELETE'
<io_opt>        ::= 'FILE' '(' ID ')'
                  | ID '(' <expr> ')'    (* INTO FROM KEY KEYTO KEYFROM TITLE *)
                  | ID                   (* INPUT OUTPUT UPDATE STREAM ...    *)

<ref_list>      ::= <ref> { ',' <ref> }
```

---

## 6. Expressions

```
<expr>          ::= <expr> <binop> <expr>
                  | <unop> <expr>
                  | '(' <expr> ')'
                  | NUMBER | STRING | BITSTRING
                  | <ref>                     (* variable, element, member,
                                                 function or builtin call *)
                  | <conversion>

<binop>         ::= '**' | '*' | '/' | '+' | '-' | '||'
                  | '=' | not-sign'=' | '<>' | '<' | '<=' | '>' | '>='
                  | not-sign'<' | not-sign'>' | '&' | '|'
<unop>          ::= '+' | '-' | not-sign

<conversion>    ::= ( 'CHAR' | 'CHARACTER' | 'BIT' | 'FIXED' | 'FLOAT'
                    | 'BINARY' | 'BIN' | 'DECIMAL' | 'DEC' | 'STRING' )
                    '(' <expr_list> ')'

<expr_list>     ::= <expr> { ',' <expr> }
```

Operator precedence, lowest to highest (comparison operators are
non-chaining in effect; `**` and the prefix operators are
right-associative, all other binaries left-associative):

| level | operators |
| --- | --- |
| 1 (lowest) | `\|` |
| 2 | `&` |
| 3 | `=  ¬=  <>  <  <=  >  >=  ¬<  ¬>` |
| 4 | `\|\|` |
| 5 | binary `+  -` |
| 6 | `*  /` |
| 7 (highest) | `**`, prefix `+  -  ¬` |

---

## 7. Embedded SQL (parsed at run time from the EXECSQL token)

```
<sql_stmt>      ::= 'CONNECT' 'TO' ID
                  | 'CONNECT' 'RESET'
                  | 'SET' 'CONNECTION' ID
                  | 'COMMIT'   [ 'WORK' ]
                  | 'ROLLBACK' [ 'WORK' ]
                  | 'DECLARE' ID 'CURSOR' [ 'WITH' 'HOLD' ] 'FOR' sql-text
                  | 'OPEN' ID
                  | 'FETCH' [ 'FROM' ] ID 'INTO' <hostvar_list>
                  | 'CLOSE' ID
                  | 'SELECT' sql-text 'INTO' <hostvar_list> sql-text
                  | 'WHENEVER' <sql_cond> <sql_action>
                  | 'INCLUDE' ID                    (* SQLCA: no-op *)
                  | sql-text                        (* pass-through DML/DDL *)

<sql_cond>      ::= 'SQLERROR' | 'SQLWARNING' | 'NOT' 'FOUND'
<sql_action>    ::= 'CONTINUE' | 'STOP'
                  | 'GOTO' ID | 'GO' 'TO' ID

<hostvar>       ::= ':' ID { '.' ID }
<hostvar_list>  ::= <hostvar> { ',' <hostvar> }
```

Host variables may appear anywhere in `sql-text` outside SQL string
literals.  Everything not matched above is sent verbatim to the
connected database.

---

## 8. Compile-time preprocessor

```
<pp_stmt>       ::= '%' ( 'DECLARE' | 'DCL' ) <pp_decl> { ',' <pp_decl> } ';'
                  | '%' ID '=' <pp_expr> ';'
                  | '%' 'ACTIVATE'   <id_list> ';'
                  | '%' 'DEACTIVATE' <id_list> ';'
                  | '%' 'INCLUDE' ( STRING | ID ) ';'
                  | '%' 'IF' <pp_expr> '%' 'THEN' <pp_unit>
                        [ '%' 'ELSE' <pp_unit> ]
                  | '%' 'DO' [ ID '=' <pp_expr> 'TO' <pp_expr>
                               [ 'BY' <pp_expr> ] ] ';'
                        source-text
                    '%' 'END' ';'                  (* loop is unrolled *)
                  | '%' 'GOTO' ID ';'              (* forward only     *)
                  | '%' ID ':' ';'                 (* preprocessor label *)

<pp_decl>       ::= ID [ 'FIXED' | 'CHARACTER' | 'CHAR' ]
<pp_unit>       ::= '%' 'DO' ';' source-text '%' 'END' ';'
                  | <pp_stmt>

<pp_expr>       ::= (* NUMBER, STRING, pp-variable names, ( ), and the
                       operators  + - * /  ||  = ^= <> < <= > >=  & |
                       with prefix - + ¬ ;  FIXED values are integers *)
```

Activated identifiers appearing in program text are replaced by their
values (CHARACTER values substitute as raw text, including any quotes
they contain).

---

## 9. Semantic restrictions not expressed in the grammar

- The top level of a `<program>` may contain only labeled procedures
  and null statements; exactly one entry procedure is executed.
- `<format_stmt>` is only meaningful with a label; `R(label)` resolves
  against it at run time.
- In `<decl_item>`, structure levels must start at 1 and members
  follow their parent contiguously; `LIKE` requires a structure.
- `SUBSTR`/`UNSPEC` as assignment targets, niladic builtins
  (`ONCODE`, `DATE`, `NULL`, ...) without parentheses, and the
  distinction array-element vs. function call are resolved
  semantically, not syntactically.
- Condition prefixes are parsed; checking is always enabled.
