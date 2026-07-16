"""PLY (ply.yacc) grammar for the PL/I subset.

Builds the AST defined in nodes.py.  The grammar covers: procedures
(nested, recursive, with parameters and RETURNS), DECLARE/DCL with
FIXED/FLOAT/BINARY/DECIMAL/CHAR/BIT/VARYING/INITIAL and array bounds,
assignment (incl. SUBSTR pseudo-variable and array elements),
IF/THEN/ELSE, DO groups (plain, WHILE/UNTIL, iterative with multiple
specifications), SELECT/WHEN/OTHERWISE, CALL, RETURN, GOTO, LEAVE,
ITERATE, PUT/GET LIST, PUT EDIT, BEGIN blocks, STOP and null statements.
"""
import ply.yacc as yacc

from .lexer import PLILexer, tokens  # noqa: F401  (tokens needed by yacc)
from . import nodes as N


class ParseError(Exception):
    """Carries every error found in the compile, not just the first:
    .messages is a list of individual error strings."""
    def __init__(self, msg, messages=None):
        super().__init__(msg)
        self.messages = messages or [msg]


MAX_ERRORS = 50

# identifier attributes that DECLARE accepts without a dedicated keyword
KNOWN_ATTRIBUTES = {
    "POINTER", "PTR", "POSITION", "BUILTIN", "EXTERNAL", "INTERNAL",
    "ALIGNED", "UNALIGNED", "REAL", "COMPLEX", "ABNORMAL", "NORMAL",
    "EVENT", "TASK",
    # file description attributes
    "STREAM", "RECORD", "INPUT", "OUTPUT", "UPDATE", "KEYED", "PRINT",
    "TITLE", "ENVIRONMENT", "ENV", "SEQUENTIAL", "DIRECT", "BUFFERED",
    "UNBUFFERED",
}


class PLIParser:
    tokens = tokens

    precedence = (
        ("nonassoc", "LOWER_THAN_ELSE"),
        ("nonassoc", "ELSE"),
        ("left", "OR"),
        ("left", "AND"),
        ("left", "EQ", "NE", "LT", "LE", "GT", "GE"),
        ("left", "CONCAT"),
        ("left", "PLUS", "MINUS"),
        ("left", "STAR", "SLASH"),
        ("right", "POW", "UMINUS", "NOT"),
    )

    # ---- program ------------------------------------------------------

    def p_program(self, p):
        "program : stmt_list"
        p[0] = p[1]

    def p_stmt_list(self, p):
        """stmt_list : stmt_list stmt
                     | empty"""
        p[0] = (p[1] + [p[2]]) if len(p) == 3 else []

    def p_empty(self, p):
        "empty :"
        p[0] = None

    # ---- statements -----------------------------------------------------

    def p_stmt_labeled(self, p):
        "stmt : ID COLON stmt"
        p[0] = N.Labeled(p[1], p[3], lineno=p.lineno(1))

    def p_stmt_prefix(self, p):
        "stmt : LPAREN id_list RPAREN COLON stmt"
        p[0] = N.Prefix(p[2], p[5], lineno=p.lineno(1))

    def p_stmt_simple(self, p):
        """stmt : assign_stmt
                | on_stmt
                | signal_stmt
                | revert_stmt
                | call_stmt
                | if_stmt
                | do_stmt
                | select_stmt
                | decl_stmt
                | put_stmt
                | get_stmt
                | format_stmt
                | goto_stmt
                | return_stmt
                | stop_stmt
                | null_stmt
                | leave_stmt
                | iterate_stmt
                | allocate_stmt
                | free_stmt
                | io_stmt
                | wait_stmt
                | proc_stmt
                | begin_stmt"""
        p[0] = p[1]

    def p_assign_stmt(self, p):
        "assign_stmt : ref EQ expr SEMI"
        p[0] = N.Assign(p[1], p[3], lineno=p[1].lineno)

    def p_ref(self, p):
        """ref : ID
               | ID LPAREN expr_list RPAREN"""
        args = p[3] if len(p) == 5 else None
        p[0] = N.Ref(p[1], args, lineno=p.lineno(1))

    def p_ref_member(self, p):
        """ref : ref DOT ID
               | ref DOT ID LPAREN expr_list RPAREN"""
        args = p[5] if len(p) == 7 else None
        p[0] = N.Member(p[1], p[3], args, lineno=p.lineno(3))

    def p_ref_pointer(self, p):
        """ref : ref ARROW ID
               | ref ARROW ID LPAREN expr_list RPAREN"""
        args = p[5] if len(p) == 7 else None
        p[0] = N.PtrRef(p[1], p[3], args, lineno=p.lineno(3))

    def p_call_stmt(self, p):
        """call_stmt : CALL ID SEMI
                     | CALL ID call_opts SEMI
                     | CALL ID LPAREN expr_list_opt RPAREN SEMI
                     | CALL ID LPAREN expr_list_opt RPAREN call_opts SEMI"""
        args, opts = [], []
        if len(p) == 5:
            opts = p[3]
        elif len(p) == 7:
            args = p[4]
        elif len(p) == 8:
            args, opts = p[4], p[6]
        p[0] = N.CallStmt(p[2], args, opts, lineno=p.lineno(1))

    def p_call_opts(self, p):
        """call_opts : call_opts call_opt
                     | call_opt"""
        p[0] = (p[1] + [p[2]]) if len(p) == 3 else [p[1]]

    def p_call_opt(self, p):
        "call_opt : ID LPAREN expr RPAREN"
        p[0] = (p[1], p[3])

    def p_wait_stmt(self, p):
        """wait_stmt : WAIT LPAREN ref_list RPAREN SEMI
                     | WAIT LPAREN ref_list RPAREN LPAREN expr RPAREN SEMI"""
        count = p[6] if len(p) == 9 else None
        p[0] = N.WaitStmt(p[3], count, lineno=p.lineno(1))

    # ---- ON conditions ----------------------------------------------------

    def p_on_stmt(self, p):
        """on_stmt : ON cond_name snap_opt stmt
                   | ON cond_name snap_opt SYSTEM SEMI"""
        unit = None if p.slice[4].type == "SYSTEM" else p[4]
        p[0] = N.On(p[2][0], p[2][1], unit, lineno=p.lineno(1))

    def p_snap_opt(self, p):
        """snap_opt : SNAP
                    | empty"""
        p[0] = p[1]

    def p_cond_name(self, p):
        """cond_name : ID
                     | ID LPAREN ID RPAREN"""
        p[0] = (p[1], p[3] if len(p) == 5 else None)

    def p_signal_stmt(self, p):
        "signal_stmt : SIGNAL cond_name SEMI"
        p[0] = N.SignalStmt(p[2][0], p[2][1], lineno=p.lineno(1))

    def p_revert_stmt(self, p):
        "revert_stmt : REVERT cond_name SEMI"
        p[0] = N.RevertStmt(p[2][0], p[2][1], lineno=p.lineno(1))

    def p_if_stmt(self, p):
        """if_stmt : IF expr THEN stmt %prec LOWER_THAN_ELSE
                   | IF expr THEN stmt ELSE stmt"""
        els = p[6] if len(p) == 7 else None
        p[0] = N.If(p[2], p[4], els, lineno=p.lineno(1))

    # ---- DO groups ------------------------------------------------------

    def p_do_stmt_plain(self, p):
        "do_stmt : DO SEMI stmt_list END opt_id SEMI"
        p[0] = N.Block(p[3], lineno=p.lineno(1))

    def p_do_stmt_cond(self, p):
        "do_stmt : DO cond_list SEMI stmt_list END opt_id SEMI"
        p[0] = N.DoWhile(p[2], p[4], lineno=p.lineno(1))

    def p_do_stmt_iter(self, p):
        "do_stmt : DO ID EQ do_spec_list SEMI stmt_list END opt_id SEMI"
        p[0] = N.DoIter(p[2], p[4], p[6], lineno=p.lineno(1))

    def p_cond_list(self, p):
        """cond_list : cond_list cond
                     | cond"""
        p[0] = (p[1] + [p[2]]) if len(p) == 3 else [p[1]]

    def p_cond(self, p):
        """cond : WHILE LPAREN expr RPAREN
                | UNTIL LPAREN expr RPAREN"""
        p[0] = (p[1].upper() if isinstance(p[1], str) else p[1], p[3])

    def p_do_spec_list(self, p):
        """do_spec_list : do_spec_list COMMA do_spec
                        | do_spec"""
        p[0] = (p[1] + [p[3]]) if len(p) == 4 else [p[1]]

    def p_do_spec(self, p):
        "do_spec : expr do_ctl_list"
        to = by = None
        conds = []
        for kind, val in p[2]:
            if kind == "TO":
                to = val
            elif kind == "BY":
                by = val
            else:
                conds.append((kind, val))
        p[0] = N.DoSpec(p[1], to, by, conds)

    def p_do_ctl_list(self, p):
        """do_ctl_list : do_ctl_list do_ctl
                       | empty"""
        p[0] = (p[1] + [p[2]]) if len(p) == 3 else []

    def p_do_ctl(self, p):
        """do_ctl : TO expr
                  | BY expr
                  | WHILE LPAREN expr RPAREN
                  | UNTIL LPAREN expr RPAREN"""
        if len(p) == 3:
            p[0] = (p[1].upper(), p[2])
        else:
            p[0] = (p[1].upper(), p[3])

    # ---- SELECT ---------------------------------------------------------

    def p_select_stmt(self, p):
        """select_stmt : SELECT LPAREN expr RPAREN SEMI when_list otherwise_opt END opt_id SEMI
                       | SELECT SEMI when_list otherwise_opt END opt_id SEMI"""
        if len(p) == 11:
            p[0] = N.Select(p[3], p[6], p[7], lineno=p.lineno(1))
        else:
            p[0] = N.Select(None, p[3], p[4], lineno=p.lineno(1))

    def p_when_list(self, p):
        """when_list : when_list when_clause
                     | empty"""
        p[0] = (p[1] + [p[2]]) if len(p) == 3 else []

    def p_when_clause(self, p):
        "when_clause : WHEN LPAREN expr_list RPAREN stmt"
        p[0] = (p[3], p[5])

    def p_otherwise_opt(self, p):
        """otherwise_opt : OTHERWISE stmt
                         | empty"""
        p[0] = p[2] if len(p) == 3 else None

    # ---- DECLARE --------------------------------------------------------

    def p_decl_stmt(self, p):
        "decl_stmt : DCLKW decl_list SEMI"
        p[0] = N.Declare(p[2], lineno=p.lineno(1))

    def p_decl_list(self, p):
        """decl_list : decl_list COMMA decl_item
                     | decl_item"""
        p[0] = (p[1] + [p[3]]) if len(p) == 4 else [p[1]]

    def p_decl_item_one(self, p):
        """decl_item : ID attr_seq
                     | ID LPAREN bound_list RPAREN attr_seq"""
        if len(p) == 3:
            p[0] = N.DeclItem([p[1]], None, p[2], None, lineno=p.lineno(1))
        else:
            p[0] = N.DeclItem([p[1]], p[3], p[5], None, lineno=p.lineno(1))

    def p_decl_item_group(self, p):
        """decl_item : LPAREN id_list RPAREN attr_seq"""
        p[0] = N.DeclItem(p[2], None, p[4], None, lineno=p.lineno(1))

    def p_decl_item_level(self, p):
        """decl_item : NUMBER ID attr_seq
                     | NUMBER ID LPAREN bound_list RPAREN attr_seq"""
        level = int(p[1])
        if len(p) == 4:
            p[0] = N.DeclItem([p[2]], None, p[3], level, lineno=p.lineno(2))
        else:
            p[0] = N.DeclItem([p[2]], p[4], p[6], level, lineno=p.lineno(2))

    def p_id_list(self, p):
        """id_list : id_list COMMA ID
                   | ID"""
        p[0] = (p[1] + [p[3]]) if len(p) == 4 else [p[1]]

    def p_bound_list(self, p):
        """bound_list : bound_list COMMA bound
                      | bound"""
        p[0] = (p[1] + [p[3]]) if len(p) == 4 else [p[1]]

    def p_bound(self, p):
        """bound : expr
                 | expr COLON expr
                 | STAR"""
        if len(p) == 2:
            p[0] = ("*",) if p[1] == "*" else (None, p[1])
        else:
            p[0] = (p[1], p[3])

    def p_attr_seq(self, p):
        """attr_seq : attr_seq attr
                    | empty"""
        p[0] = (p[1] + [p[2]]) if len(p) == 3 else []

    def p_attr_type(self, p):
        """attr : FIXED prec_opt
                | FLOAT prec_opt
                | BINKW prec_opt
                | DECKW prec_opt"""
        p[0] = (p.slice[1].type, p[2])

    def p_attr_char(self, p):
        """attr : CHARKW LPAREN length RPAREN
                | CHARKW"""
        p[0] = ("CHARKW", p[3] if len(p) == 5 else N.Num(1))

    def p_attr_bit(self, p):
        """attr : BIT LPAREN length RPAREN
                | BIT"""
        p[0] = ("BIT", p[3] if len(p) == 5 else N.Num(1))

    def p_length(self, p):
        """length : expr
                  | STAR"""
        p[0] = "*" if p[1] == "*" else p[1]

    def p_attr_simple(self, p):
        """attr : VARYING
                | STATIC
                | AUTOMATIC
                | LABEL"""
        p[0] = (p.slice[1].type, None)

    def p_attr_init(self, p):
        "attr : INITKW LPAREN expr_list RPAREN"
        p[0] = ("INIT", p[3])

    def p_attr_like(self, p):
        "attr : LIKE ref"
        p[0] = ("LIKE", p[2])

    def p_attr_picture(self, p):
        "attr : PICTKW STRING"
        p[0] = ("PICTURE", p[2])

    def p_attr_storage(self, p):
        """attr : BASED
                | BASED LPAREN ref RPAREN
                | CONTROLLED
                | DEFINED ref
                | DEFINED LPAREN ref RPAREN"""
        t = p.slice[1].type
        if t == "BASED":
            p[0] = ("BASED", p[3] if len(p) == 5 else None)
        elif t == "CONTROLLED":
            p[0] = ("CONTROLLED", None)
        else:
            p[0] = ("DEFINED", p[3] if len(p) == 5 else p[2])

    def p_attr_generic(self, p):
        """attr : ID
                | ID LPAREN expr_list RPAREN"""
        if p[1] not in KNOWN_ATTRIBUTES:
            self._err("line %d: unknown attribute %r in DECLARE"
                      % (p.lineno(1), p[1]))
        p[0] = ("GENERIC", (p[1], p[3] if len(p) == 5 else None))

    def p_attr_file(self, p):
        "attr : FILE"
        p[0] = ("FILE", None)

    def p_allocate_stmt(self, p):
        "allocate_stmt : ALLOCATE alloc_list SEMI"
        p[0] = N.AllocStmt(p[2], lineno=p.lineno(1))

    def p_alloc_list(self, p):
        """alloc_list : alloc_list COMMA alloc_item
                      | alloc_item"""
        p[0] = (p[1] + [p[3]]) if len(p) == 4 else [p[1]]

    def p_alloc_item(self, p):
        """alloc_item : ID
                      | ID ID LPAREN ref RPAREN"""
        if len(p) == 2:
            p[0] = (p[1], None)
        else:
            if p[2] != "SET":
                self._err("line %d: expected SET, found %r"
                          % (p.lineno(2), p[2]))
            p[0] = (p[1], p[4])

    def p_free_stmt(self, p):
        "free_stmt : FREE id_list SEMI"
        p[0] = N.FreeStmt(p[2], lineno=p.lineno(1))

    # ---- record I/O -----------------------------------------------------

    def p_io_stmt(self, p):
        """io_stmt : OPEN io_opts SEMI
                   | CLOSE io_opts SEMI
                   | READ io_opts SEMI
                   | WRITE io_opts SEMI
                   | REWRITE io_opts SEMI
                   | DELETE io_opts SEMI"""
        p[0] = N.IOStmt(p.slice[1].type, p[2], lineno=p.lineno(1))

    def p_io_opts(self, p):
        """io_opts : io_opts io_opt
                   | io_opt"""
        p[0] = (p[1] + [p[2]]) if len(p) == 3 else [p[1]]

    def p_io_opt(self, p):
        """io_opt : FILE LPAREN ID RPAREN
                  | ID LPAREN expr RPAREN
                  | ID"""
        if p.slice[1].type == "FILE":
            p[0] = ("FILE", p[3])
        elif len(p) == 5:
            p[0] = (p[1], p[3])
        else:
            p[0] = (p[1], None)

    def p_prec_opt(self, p):
        """prec_opt : LPAREN NUMBER RPAREN
                    | LPAREN NUMBER COMMA NUMBER RPAREN
                    | empty"""
        if len(p) == 4:
            p[0] = (p[2], 0)
        elif len(p) == 6:
            p[0] = (p[2], p[4])
        else:
            p[0] = None

    # ---- PUT / GET ------------------------------------------------------

    def p_put_stmt(self, p):
        "put_stmt : PUT put_clause_list SEMI"
        p[0] = N.Put(p[2], lineno=p.lineno(1))

    def p_put_clause_list(self, p):
        """put_clause_list : put_clause_list put_clause
                           | empty"""
        p[0] = (p[1] + [p[2]]) if len(p) == 3 else []

    def p_put_clause(self, p):
        """put_clause : PAGE
                      | SKIP
                      | SKIP LPAREN expr RPAREN
                      | LIST LPAREN expr_list RPAREN
                      | EDIT LPAREN expr_list RPAREN LPAREN format_list RPAREN"""
        kw = p.slice[1].type
        if kw == "PAGE":
            p[0] = ("PAGE",)
        elif kw == "SKIP":
            p[0] = ("SKIP", p[3] if len(p) == 5 else None)
        elif kw == "LIST":
            p[0] = ("LIST", p[3])
        else:
            p[0] = ("EDIT", p[3], p[6])

    def p_put_clause_data(self, p):
        """put_clause : DATA
                      | DATA LPAREN ref_list RPAREN
                      | STRINGKW LPAREN ref RPAREN
                      | FILE LPAREN ID RPAREN"""
        t = p.slice[1].type
        if t == "DATA":
            p[0] = ("DATA", p[3] if len(p) == 5 else None)
        elif t == "FILE":
            p[0] = ("FILE", p[3])
        else:
            p[0] = ("STRING", p[3])

    def p_format_stmt(self, p):
        "format_stmt : FORMAT LPAREN format_list RPAREN SEMI"
        p[0] = N.FormatStmt(p[3], lineno=p.lineno(1))

    def p_format_list(self, p):
        """format_list : format_list COMMA format_item
                       | format_item"""
        p[0] = (p[1] + [p[3]]) if len(p) == 4 else [p[1]]

    def p_format_item_pic(self, p):
        "format_item : ID STRING"
        if p[1] != "P":
            self._err("line %d: unknown format item %s'...'"
                      % (p.lineno(1), p[1]))
        p[0] = N.FormatItem("P", [N.Str(p[2])])

    def p_format_item(self, p):
        """format_item : ID
                       | ID LPAREN expr_list RPAREN
                       | SKIP
                       | SKIP LPAREN expr RPAREN"""
        name = p[1] if p.slice[1].type == "ID" else "SKIP"
        if len(p) == 2:
            p[0] = N.FormatItem(name, [])
        elif p.slice[1].type == "SKIP":
            p[0] = N.FormatItem(name, [p[3]])
        else:
            p[0] = N.FormatItem(name, p[3])

    def p_get_stmt(self, p):
        "get_stmt : GET get_clause_list SEMI"
        p[0] = N.Get(p[2], lineno=p.lineno(1))

    def p_get_clause_list(self, p):
        """get_clause_list : get_clause_list get_clause
                           | empty"""
        p[0] = (p[1] + [p[2]]) if len(p) == 3 else []

    def p_get_clause(self, p):
        """get_clause : SKIP
                      | SKIP LPAREN expr RPAREN
                      | LIST LPAREN ref_list RPAREN"""
        if p.slice[1].type == "SKIP":
            p[0] = ("SKIP", p[3] if len(p) == 5 else None)
        else:
            p[0] = ("LIST", p[3])

    def p_get_clause_data(self, p):
        """get_clause : DATA
                      | DATA LPAREN ref_list RPAREN
                      | STRINGKW LPAREN expr RPAREN
                      | EDIT LPAREN ref_list RPAREN LPAREN format_list RPAREN"""
        t = p.slice[1].type
        if t == "DATA":
            p[0] = ("DATA", p[3] if len(p) == 5 else None)
        elif t == "STRINGKW":
            p[0] = ("STRING", p[3])
        else:
            p[0] = ("EDIT", p[3], p[6])

    def p_get_clause_file(self, p):
        "get_clause : FILE LPAREN ID RPAREN"
        p[0] = ("FILE", p[3])

    def p_ref_list(self, p):
        """ref_list : ref_list COMMA ref
                    | ref"""
        p[0] = (p[1] + [p[3]]) if len(p) == 4 else [p[1]]

    # ---- flow control ---------------------------------------------------

    def p_goto_stmt(self, p):
        """goto_stmt : GOTO ID SEMI
                     | GO TO ID SEMI"""
        p[0] = N.Goto(p[2] if len(p) == 4 else p[3], lineno=p.lineno(1))

    def p_return_stmt(self, p):
        """return_stmt : RETURN SEMI
                       | RETURN expr SEMI"""
        p[0] = N.Return(p[2] if len(p) == 4 else None, lineno=p.lineno(1))

    def p_stop_stmt(self, p):
        "stop_stmt : STOP SEMI"
        p[0] = N.Stop(lineno=p.lineno(1))

    def p_null_stmt(self, p):
        "null_stmt : SEMI"
        p[0] = N.Null(lineno=p.lineno(1))

    def p_leave_stmt(self, p):
        "leave_stmt : LEAVE opt_id SEMI"
        p[0] = N.Leave(p[2], lineno=p.lineno(1))

    def p_iterate_stmt(self, p):
        "iterate_stmt : ITERATE opt_id SEMI"
        p[0] = N.Iterate(p[2], lineno=p.lineno(1))

    def p_opt_id(self, p):
        """opt_id : ID
                  | empty"""
        p[0] = p[1]

    # ---- procedures / begin blocks ---------------------------------------

    def p_proc_stmt(self, p):
        "proc_stmt : PROC proc_attr_list SEMI stmt_list END opt_id SEMI"
        params, returns, options, recursive = [], None, [], False
        for kind, val in p[2]:
            if kind == "PARAMS":
                params = val
            elif kind == "RETURNS":
                returns = val
            elif kind == "OPTIONS":
                options = val
            elif kind == "RECURSIVE":
                recursive = True
        p[0] = N.ProcDef(params, returns, options, recursive, p[4],
                         lineno=p.lineno(1))

    def p_proc_attr_list(self, p):
        """proc_attr_list : proc_attr_list proc_attr
                          | empty"""
        p[0] = (p[1] + [p[2]]) if len(p) == 3 else []

    def p_proc_attr(self, p):
        """proc_attr : LPAREN id_list RPAREN
                     | OPTIONS LPAREN id_list RPAREN
                     | RETURNS LPAREN attr_seq RPAREN
                     | ID"""
        t = p.slice[1].type
        if t == "LPAREN":
            p[0] = ("PARAMS", p[2])
        elif t == "OPTIONS":
            p[0] = ("OPTIONS", p[3])
        elif t == "RETURNS":
            p[0] = ("RETURNS", p[3])
        else:
            if p[1] != "RECURSIVE":
                self._err("line %d: unknown procedure attribute %r"
                          % (p.lineno(1), p[1]))
            p[0] = ("RECURSIVE", True)

    def p_begin_stmt(self, p):
        "begin_stmt : BEGIN SEMI stmt_list END opt_id SEMI"
        p[0] = N.BeginBlock(p[3], lineno=p.lineno(1))

    # ---- expressions ------------------------------------------------------

    def p_expr_binop(self, p):
        """expr : expr PLUS expr
                | expr MINUS expr
                | expr STAR expr
                | expr SLASH expr
                | expr POW expr
                | expr CONCAT expr
                | expr EQ expr
                | expr NE expr
                | expr LT expr
                | expr LE expr
                | expr GT expr
                | expr GE expr
                | expr AND expr
                | expr OR expr"""
        p[0] = N.BinOp(p.slice[2].type, p[1], p[3], lineno=p.lineno(2))

    def p_expr_unary(self, p):
        """expr : MINUS expr %prec UMINUS
                | PLUS expr %prec UMINUS
                | NOT expr"""
        p[0] = N.UnOp(p.slice[1].type, p[2], lineno=p.lineno(1))

    def p_expr_group(self, p):
        "expr : LPAREN expr RPAREN"
        p[0] = p[2]

    def p_expr_number(self, p):
        "expr : NUMBER"
        p[0] = N.Num(p[1], lineno=p.lineno(1))

    def p_expr_string(self, p):
        "expr : STRING"
        p[0] = N.Str(p[1], lineno=p.lineno(1))

    def p_expr_bits(self, p):
        "expr : BITSTRING"
        p[0] = N.Bits(p[1], lineno=p.lineno(1))

    def p_expr_ref(self, p):
        "expr : ref"
        p[0] = p[1]

    def p_expr_conversion(self, p):
        """expr : CHARKW LPAREN expr_list RPAREN
                | BIT LPAREN expr_list RPAREN
                | FIXED LPAREN expr_list RPAREN
                | FLOAT LPAREN expr_list RPAREN
                | BINKW LPAREN expr_list RPAREN
                | DECKW LPAREN expr_list RPAREN
                | STRINGKW LPAREN expr_list RPAREN"""
        name = {"CHARKW": "CHAR", "BINKW": "BINARY", "DECKW": "DECIMAL",
                "STRINGKW": "STRING"}.get(p.slice[1].type, p.slice[1].type)
        p[0] = N.Ref(name, p[3], lineno=p.lineno(1))

    def p_expr_list(self, p):
        """expr_list : expr_list COMMA expr
                     | expr"""
        p[0] = (p[1] + [p[3]]) if len(p) == 4 else [p[1]]

    def p_expr_list_opt(self, p):
        """expr_list_opt : expr_list
                         | empty"""
        p[0] = p[1] if p[1] is not None else []

    def p_stmt_error(self, p):
        "stmt : error SEMI"
        # panic-mode recovery: the offending statement becomes a null
        # statement and parsing resumes after its semicolon
        p[0] = N.Null(lineno=0)

    def _err(self, msg):
        if len(self.errors) < MAX_ERRORS:
            self.errors.append(msg)

    def p_error(self, tok):
        if tok is None:
            self._err("unexpected end of input")
            return
        self._err("line %d: syntax error near %r" % (tok.lineno, tok.value))
        # returning lets yacc discard tokens until 'stmt : error SEMI'
        # can apply, so the remaining statements are still checked

    # ---- driver -----------------------------------------------------------

    def build(self, **kwargs):
        kwargs.setdefault("write_tables", False)
        kwargs.setdefault("debug", False)
        self.lexer_obj = PLILexer()
        self.lexer_obj.build()
        self.parser = yacc.yacc(module=self, **kwargs)
        return self.parser

    def parse(self, text):
        if not hasattr(self, "parser"):
            self.build()
        self.errors = []
        self.lexer_obj.lexer.lineno = 1
        result = self.parser.parse(text, lexer=self.lexer_obj.lexer)
        if self.errors:
            if len(self.errors) >= MAX_ERRORS:
                self.errors.append("(further errors suppressed)")
            raise ParseError("\n".join(self.errors), list(self.errors))
        return result


Parser = PLIParser


def parse(text, **kwargs):
    return PLIParser().parse(text)
