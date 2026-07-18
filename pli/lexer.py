"""PLY (ply.lex) tokenizer for PL/I source.

PL/I is case-insensitive; identifiers are upper-cased and a fixed set of
statement keywords is reserved (real PL/I has no reserved words, but a
reserved set keeps the yacc grammar LALR(1)).  Handles /* */ comments,
'string''escapes', '1010'B bit strings, numeric constants with exponents
and binary B suffix, and the NOT symbol spelled ¬, ^ or ~.
"""
import ply.lex as lex


class LexError(Exception):
    pass


# keyword text -> token type (synonyms map to one token)
reserved = {
    "PROCEDURE": "PROC", "PROC": "PROC",
    "DECLARE": "DCLKW", "DCL": "DCLKW",
    "CHARACTER": "CHARKW", "CHAR": "CHARKW",
    "INITIAL": "INITKW", "INIT": "INITKW",
    "BINARY": "BINKW", "BIN": "BINKW",
    "DECIMAL": "DECKW", "DEC": "DECKW",
    "OTHERWISE": "OTHERWISE", "OTHER": "OTHERWISE",
    "IF": "IF", "THEN": "THEN", "ELSE": "ELSE",
    "DO": "DO", "END": "END", "TO": "TO", "BY": "BY",
    "WHILE": "WHILE", "UNTIL": "UNTIL",
    "SELECT": "SELECT", "WHEN": "WHEN",
    "CALL": "CALL", "RETURN": "RETURN", "RETURNS": "RETURNS",
    "GOTO": "GOTO", "GO": "GO",
    "PUT": "PUT", "GET": "GET", "LIST": "LIST", "EDIT": "EDIT",
    "SKIP": "SKIP", "PAGE": "PAGE",
    "STOP": "STOP", "BEGIN": "BEGIN",
    "LEAVE": "LEAVE", "ITERATE": "ITERATE",
    "OPTIONS": "OPTIONS",
    "FIXED": "FIXED", "FLOAT": "FLOAT", "BIT": "BIT",
    "VARYING": "VARYING", "STATIC": "STATIC", "AUTOMATIC": "AUTOMATIC",
    "LABEL": "LABEL",
    "ON": "ON", "SIGNAL": "SIGNAL", "REVERT": "REVERT",
    "SYSTEM": "SYSTEM", "SNAP": "SNAP", "LIKE": "LIKE",
    "DATA": "DATA", "FORMAT": "FORMAT",
    "PICTURE": "PICTKW", "PIC": "PICTKW",
    "STRING": "STRINGKW",
    "ALLOCATE": "ALLOCATE", "ALLOC": "ALLOCATE", "FREE": "FREE",
    "BASED": "BASED", "CONTROLLED": "CONTROLLED", "CTL": "CONTROLLED",
    "DEFINED": "DEFINED", "DEF": "DEFINED",
    "FILE": "FILE", "OPEN": "OPEN", "CLOSE": "CLOSE",
    "READ": "READ", "WRITE": "WRITE", "REWRITE": "REWRITE",
    "DELETE": "DELETE",
    "WAIT": "WAIT",
}

tokens = [
    "ID", "NUMBER", "STRING", "BITSTRING", "EXECSQL",
    "POW", "CONCAT", "NE", "LE", "GE", "EQ", "LT", "GT", "ARROW",
    "PLUS", "MINUS", "STAR", "SLASH", "AND", "OR", "NOT",
    "LPAREN", "RPAREN", "COMMA", "SEMI", "COLON", "DOT",
] + sorted(set(reserved.values()))


class PLILexer:
    tokens = tokens

    t_ignore = " \t\r\f\v"

    def t_comment(self, t):
        r"/\*(.|\n)*?\*/"
        t.lexer.lineno += t.value.count("\n")

    def t_newline(self, t):
        r"\n+"
        t.lexer.lineno += len(t.value)

    # strings before anything else; a doubled '' is a literal quote,
    # a B suffix makes it a bit-string constant
    def t_STRING(self, t):
        r"'([^']|'')*'[Bb]?"
        raw = t.value
        if raw[-1] in "Bb":
            body = raw[1:-2].replace("''", "'")
            if any(c not in "01" for c in body):
                raise LexError("line %d: invalid bit string %r"
                               % (t.lexer.lineno, body))
            t.type = "BITSTRING"
            t.value = body
        else:
            t.value = raw[1:-1].replace("''", "'")
        return t

    def t_NUMBER(self, t):
        r"(\d+\.\d*|\.\d+|\d+)([Ee][+-]?\d+)?[BbIi]?"
        from .fixeddec import FixedDec
        raw = t.value
        if raw[-1] in "Bb":
            digits = raw[:-1]
            if any(c not in "01" for c in digits):
                raise LexError("line %d: invalid binary constant %r"
                               % (t.lexer.lineno, raw))
            t.value = int(digits, 2)
        elif raw[-1] in "Ii":                    # imaginary constant 3I
            body = raw[:-1]
            t.value = complex(0.0, float(body))
        elif "E" in raw.upper():
            t.value = float(raw)
        elif "." in raw:
            t.value = FixedDec.from_literal(raw)  # exact FIXED DECIMAL
        else:
            t.value = int(raw)
        return t

    def t_EXECSQL(self, t):
        r"[Ee][Xx][Ee][Cc]\s+[Ss][Qq][Ll]\b(?:'[^']*'|[^;'])*;"
        # embedded SQL: everything up to the (quote-aware) semicolon is
        # kept as opaque text for the SQL runtime; not parsed as PL/I
        t.lexer.lineno += t.value.count("\n")
        body = t.value[t.value.upper().index("SQL") + 3:-1]
        t.value = body.strip()
        return t

    def t_ID(self, t):
        r"[A-Za-z_$\#@][A-Za-z0-9_$\#@]*"
        t.value = t.value.upper()
        t.type = reserved.get(t.value, "ID")
        return t

    # multi-char operators (function rules keep definition order)
    def t_POW(self, t):
        r"\*\*"
        return t

    def t_CONCAT(self, t):
        r"\|\|"
        return t

    def t_ARROW(self, t):
        r"->"
        return t

    def t_NE(self, t):
        r"[¬^~]=|<>"
        return t

    def t_LE(self, t):
        r"<=|[¬^~]>"
        return t

    def t_GE(self, t):
        r">=|[¬^~]<"
        return t

    def t_NOT(self, t):
        r"[¬^~]"
        return t

    t_EQ = r"="
    t_LT = r"<"
    t_GT = r">"
    t_PLUS = r"\+"
    t_MINUS = r"-"
    t_STAR = r"\*"
    t_SLASH = r"/"
    t_AND = r"&"
    t_OR = r"\|"
    t_LPAREN = r"\("
    t_RPAREN = r"\)"
    t_COMMA = r","
    t_SEMI = r";"
    t_COLON = r":"
    t_DOT = r"\."

    def t_error(self, t):
        raise LexError("line %d: illegal character %r"
                       % (t.lexer.lineno, t.value[0]))

    def build(self, **kwargs):
        self.lexer = lex.lex(module=self, **kwargs)
        return self.lexer


def make_lexer():
    return PLILexer().build()


# convenience alias used by pli/__init__.py
Lexer = PLILexer


class Token:  # kept for API compatibility
    pass
