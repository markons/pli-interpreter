"""PL/I compile-time preprocessor (the % statements).

Supported:
  %DECLARE name FIXED|CHARACTER [, ...];
  %name = expression;
  %ACTIVATE name [, ...];   %DEACTIVATE name [, ...];
  %INCLUDE 'filename';      %INCLUDE name;
  %IF expr %THEN unit [%ELSE unit];   unit = %DO; text %END; or a
                                       single % statement
  %DO name = e1 TO e2 [BY e3]; text %END;   (loop is unrolled)
  %label: ;  and  %GOTO label;  (forward skip only)

Activated names appearing in program text are replaced by their values.
Preprocessor expressions support + - * / || comparisons & | ¬ and
parentheses over FIXED (int) and CHARACTER (str) values.

This is a single-pass implementation: replacement output is not
re-scanned, and %GOTO may only jump forward (the F compiler allows
backward jumps; use %DO loops instead here).
"""
import os
import re


class PreprocError(Exception):
    pass


_TOKEN_RE = re.compile(r"""
    (?P<comment>/\*.*?\*/)
  | (?P<string>'(?:[^']|'')*')
  | (?P<name>[A-Za-z_$\#@][A-Za-z0-9_$\#@]*)
  | (?P<number>\d+)
  | (?P<pct>%)
  | (?P<ws>\s+)
  | (?P<op><=|>=|\^=|~=|¬=|<>|\|\||.)
""", re.VERBOSE | re.DOTALL)


def _tokenize(text):
    out = []
    for m in _TOKEN_RE.finditer(text):
        kind = m.lastgroup
        out.append((kind, m.group()))
    return out


class Preprocessor:
    def __init__(self, include_dir="."):
        self.include_dir = include_dir
        self.vars = {}        # name -> (type, value)
        self.active = set()

    def process(self, text):
        toks = _tokenize(text)
        return "".join(self._run(toks))

    # ---- main loop -------------------------------------------------------

    def _run(self, toks):
        out = []
        i = 0
        while i < len(toks):
            kind, val = toks[i]
            if kind == "pct":
                i = self._pp_statement(toks, i + 1, out)
            elif kind == "name" and val.upper() in self.active:
                typ, value = self.vars[val.upper()]
                if typ == "PROC":
                    j = self._skip_ws(toks, i + 1)
                    if j < len(toks) and toks[j][1] == "(":
                        args, i = self._collect_call_args(toks, j)
                        result = self._exec_proc(val.upper(), args)
                        out.extend(self._run(_tokenize(str(result))))
                        continue
                    out.append(val)      # no argument list: leave as-is
                    i += 1
                    continue
                out.append(str(value))
                i += 1
            else:
                out.append(val)
                i += 1
        return out

    def _skip_ws(self, toks, i):
        while i < len(toks) and toks[i][0] in ("ws", "comment"):
            i += 1
        return i

    def _until_semi(self, toks, i):
        """Return (tokens-before-';', index-after-';')."""
        seg = []
        while i < len(toks):
            if toks[i][0] == "op" and toks[i][1] == ";":
                return seg, i + 1
            seg.append(toks[i])
            i += 1
        raise PreprocError("unterminated % statement")

    # ---- statements -------------------------------------------------------

    def _pp_statement(self, toks, i, out):
        i = self._skip_ws(toks, i)
        if i >= len(toks):
            raise PreprocError("dangling %")
        kind, val = toks[i]
        word = val.upper() if kind == "name" else ""
        if word in ("DECLARE", "DCL"):
            seg, i = self._until_semi(toks, i + 1)
            self._pp_declare(seg)
            return i
        if word == "ACTIVATE":
            seg, i = self._until_semi(toks, i + 1)
            for n in self._name_list(seg):
                self.active.add(n)
            return i
        if word == "DEACTIVATE":
            seg, i = self._until_semi(toks, i + 1)
            for n in self._name_list(seg):
                self.active.discard(n)
            return i
        if word == "INCLUDE":
            seg, i = self._until_semi(toks, i + 1)
            seg = [t for t in seg if t[0] not in ("ws", "comment")]
            if not seg:
                raise PreprocError("%INCLUDE: missing name")
            k, v = seg[0]
            fname = v[1:-1].replace("''", "'") if k == "string" else v
            if not os.path.splitext(fname)[1]:
                fname += ".pli"
            path = os.path.join(self.include_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as h:
                    text = h.read()
            except OSError as e:
                raise PreprocError("%%INCLUDE %s: %s" % (fname, e))
            out.extend(self._run(_tokenize(text)))
            return i
        if word == "IF":
            return self._pp_if(toks, i + 1, out)
        if word == "DO":
            return self._pp_do(toks, i + 1, out)
        if word == "GOTO":
            seg, i = self._until_semi(toks, i + 1)
            names = self._name_list(seg)
            if not names:
                raise PreprocError("%GOTO: missing label")
            return self._skip_to_label(toks, i, names[0])
        if word in self.vars or (kind == "name" and word not in
                                 ("THEN", "ELSE", "END")):
            # %name = expr;   or   %label: ;   or   %name: PROC...
            j = self._skip_ws(toks, i + 1)
            if j < len(toks) and toks[j][0] == "op" and toks[j][1] == ":":
                k = self._skip_ws(toks, j + 1)
                if k < len(toks) and toks[k][0] == "name" and \
                        toks[k][1].upper() in ("PROC", "PROCEDURE"):
                    return self._pp_proc_def(word, toks, k + 1)
                _, i2 = self._until_semi(toks, j + 1)
                return i2                     # label definition: no output
            seg, i = self._until_semi(toks, i + 1)
            seg = [t for t in seg if t[0] not in ("ws", "comment")]
            if not seg or seg[0][1] != "=":
                raise PreprocError("bad %% statement near %s" % val)
            value = self._eval(seg[1:])
            typ = self.vars.get(word, ("FIXED", 0))[0]
            if typ == "FIXED":
                value = int(value) if not isinstance(value, str) \
                    else int(value.strip() or 0)
            else:
                value = str(value)
            self.vars[word] = (typ, value)
            return i
        raise PreprocError("unknown %% statement near %r" % val)

    def _pp_declare(self, seg):
        seg = [t for t in seg if t[0] not in ("ws", "comment")]
        i = 0
        while i < len(seg):
            if seg[i][0] != "name":
                raise PreprocError("%DECLARE: expected a name")
            name = seg[i][1].upper()
            i += 1
            typ = "FIXED"
            if i < len(seg) and seg[i][0] == "name":
                t = seg[i][1].upper()
                if t in ("CHARACTER", "CHAR"):
                    typ = "CHARACTER"
                    i += 1
                elif t == "FIXED":
                    i += 1
            self.vars[name] = (typ, 0 if typ == "FIXED" else "")
            self.active.add(name)
            if i < len(seg) and seg[i][1] == ",":
                i += 1

    def _name_list(self, seg):
        return [v.upper() for k, v in seg if k == "name"]

    # ---- preprocessor procedures (%name: PROC ... %END;) ----------------

    def _pp_proc_def(self, name, toks, i):
        """Parse %name: PROC(parms) [RETURNS(...)]; body %END;
        Body statements are written WITHOUT the %% sign (F style):
        assignment, IF/THEN/ELSE, DO;...END;, DO v=a TO b [BY c];,
        RETURN(expr);"""
        header, i = self._until_semi(toks, i)
        header = [t for t in header if t[0] not in ("ws", "comment")]
        params = []
        j = 0
        if j < len(header) and header[j][1] == "(":
            j += 1
            while j < len(header) and header[j][1] != ")":
                if header[j][0] == "name":
                    params.append(header[j][1].upper())
                j += 1
        # RETURNS(...) in the header is accepted and ignored
        body = []
        depth = 0
        while i < len(toks):
            if toks[i][0] == "pct":
                j2 = self._skip_ws(toks, i + 1)
                if j2 < len(toks) and toks[j2][0] == "name":
                    w = toks[j2][1].upper()
                    if w in ("DO", "IF", "PROC", "PROCEDURE"):
                        depth += 1
                    elif w == "END":
                        if depth == 0:
                            _, i = self._until_semi(toks, j2 + 1)
                            break
                        depth -= 1
            body.append(toks[i])
            i += 1
        else:
            raise PreprocError("%%%s: missing %%END" % name)
        stmts = self._parse_proc_body(
            [t for t in body if t[0] not in ("ws", "comment")])
        self.vars[name] = ("PROC", (params, stmts))
        self.active.add(name)
        return i

    def _parse_proc_body(self, toks):
        self._bt, self._bp = toks, 0
        stmts = []
        while self._bp < len(self._bt):
            stmts.append(self._parse_stmt())
        return stmts

    def _bpeek(self):
        return self._bt[self._bp] if self._bp < len(self._bt) \
            else (None, None)

    def _bnext(self):
        t = self._bpeek()
        self._bp += 1
        return t

    def _bexpect(self, text):
        k, v = self._bnext()
        if v is None or v.upper() != text.upper() and v != text:
            raise PreprocError("%%PROC body: expected %r, found %r"
                               % (text, v))

    def _collect_expr(self, stops):
        out = []
        depth = 0
        while self._bp < len(self._bt):
            k, v = self._bpeek()
            u = v.upper() if k == "name" else v
            if depth == 0 and u in stops:
                break
            if v == "(":
                depth += 1
            elif v == ")":
                if depth == 0:
                    break
                depth -= 1
            out.append(self._bnext())
        return out

    def _parse_stmt(self):
        k, v = self._bnext()
        if k == "name":
            w = v.upper()
            if w == "RETURN":
                self._bexpect("(")
                expr = self._collect_expr({")"})
                self._bexpect(")")
                self._bexpect(";")
                return ("RETURN", expr)
            if w == "IF":
                cond = self._collect_expr({"THEN"})
                self._bexpect("THEN")
                then = self._parse_stmt()
                els = None
                nk, nv = self._bpeek()
                if nk == "name" and nv.upper() == "ELSE":
                    self._bnext()
                    els = self._parse_stmt()
                return ("IF", cond, then, els)
            if w == "DO":
                nk, nv = self._bpeek()
                if nv == ";":
                    self._bnext()
                    return ("BLOCK", self._parse_group())
                var = None
                if nk == "name":
                    var = self._bnext()[1].upper()
                    self._bexpect("=")
                    start = self._collect_expr({"TO"})
                    self._bexpect("TO")
                    limit = self._collect_expr({"BY", ";"})
                    step = None
                    if self._bpeek()[1] and \
                            self._bpeek()[1].upper() == "BY":
                        self._bnext()
                        step = self._collect_expr({";"})
                    self._bexpect(";")
                    return ("LOOP", var, start, limit, step,
                            self._parse_group())
                raise PreprocError("%PROC body: bad DO")
            if w in ("DCL", "DECLARE"):
                seg = self._collect_expr({";"})
                self._bexpect(";")
                decls = []
                nm = None
                for k2, v2 in seg:
                    if k2 != "name":
                        continue
                    u = v2.upper()
                    if u in ("FIXED", "CHARACTER", "CHAR"):
                        if nm:
                            decls.append((nm, "FIXED" if u == "FIXED"
                                          else "CHARACTER"))
                            nm = None
                    else:
                        if nm:
                            decls.append((nm, "FIXED"))
                        nm = u
                if nm:
                    decls.append((nm, "FIXED"))
                return ("DCL", decls)
            if self._bpeek()[1] == "=":
                self._bnext()
                expr = self._collect_expr({";"})
                self._bexpect(";")
                return ("SET", w, expr)
        if v == ";":
            return ("NULL",)
        raise PreprocError("%%PROC body: unexpected %r" % v)

    def _parse_group(self):
        stmts = []
        while True:
            k, v = self._bpeek()
            if k == "name" and v.upper() == "END":
                self._bnext()
                self._bexpect(";")
                return stmts
            if k is None:
                raise PreprocError("%PROC body: missing END")
            stmts.append(self._parse_stmt())

    class _PPReturn(Exception):
        def __init__(self, value):
            self.value = value

    def _exec_proc(self, name, arg_texts):
        params, stmts = self.vars[name][1]
        saved = dict(self.vars)
        try:
            for p_, a in zip(params, arg_texts):
                toks = [t for t in _tokenize(a)
                        if t[0] not in ("ws", "comment")]
                try:
                    val = self._eval(toks)
                except PreprocError:
                    val = a.strip()
                self.vars[p_] = ("FIXED" if isinstance(val, int)
                                 else "CHARACTER", val)
            try:
                for s in stmts:
                    self._exec_pp_stmt(s)
            except Preprocessor._PPReturn as r:
                return r.value
            return ""
        finally:
            self.vars = saved

    def _exec_pp_stmt(self, s):
        kind = s[0]
        if kind == "RETURN":
            raise Preprocessor._PPReturn(self._eval(list(s[1])))
        if kind == "SET":
            val = self._eval(list(s[2]))
            typ = self.vars.get(s[1], ("FIXED", 0))[0]
            if typ == "FIXED" and not isinstance(val, str):
                val = int(val)
            self.vars[s[1]] = (typ if typ in ("FIXED", "CHARACTER")
                               else "FIXED", val)
            return
        if kind == "IF":
            if self._truthy(self._eval(list(s[1]))):
                self._exec_pp_stmt(s[2])
            elif s[3] is not None:
                self._exec_pp_stmt(s[3])
            return
        if kind == "BLOCK":
            for t in s[1]:
                self._exec_pp_stmt(t)
            return
        if kind == "LOOP":
            v = int(self._eval(list(s[2])))
            limit = int(self._eval(list(s[3])))
            step = int(self._eval(list(s[4]))) if s[4] else 1
            while (step >= 0 and v <= limit) or (step < 0 and v >= limit):
                self.vars[s[1]] = ("FIXED", v)
                for t in s[5]:
                    self._exec_pp_stmt(t)
                v += step
            return
        if kind == "DCL":
            for nm, typ in s[1]:
                self.vars[nm] = (typ, 0 if typ == "FIXED" else "")
            return
        if kind == "NULL":
            return
        raise PreprocError("bad %%PROC statement %r" % (kind,))

    def _collect_call_args(self, toks, i):
        """Raw argument texts of name(...) starting at the '('."""
        depth = 0
        args = []
        cur = []
        while i < len(toks):
            k, v = toks[i]
            if v == "(" and k == "op":
                depth += 1
                if depth == 1:
                    i += 1
                    continue
            elif v == ")" and k == "op":
                depth -= 1
                if depth == 0:
                    args.append("".join(t[1] for t in cur))
                    return args, i + 1
            elif v == "," and k == "op" and depth == 1:
                args.append("".join(t[1] for t in cur))
                cur = []
                i += 1
                continue
            cur.append(toks[i])
            i += 1
        raise PreprocError("unbalanced ( in preprocessor call")

    def _skip_to_label(self, toks, i, label):
        while i < len(toks):
            if toks[i][0] == "pct":
                j = self._skip_ws(toks, i + 1)
                if j < len(toks) and toks[j][0] == "name" \
                        and toks[j][1].upper() == label:
                    k = self._skip_ws(toks, j + 1)
                    if k < len(toks) and toks[k][1] == ":":
                        _, end = self._until_semi(toks, k + 1)
                        return end
            i += 1
        raise PreprocError("%%GOTO: label %s not found (forward only)"
                           % label)

    # ---- %IF / %DO --------------------------------------------------------

    def _pp_unit(self, toks, i, out):
        """A %THEN/%ELSE unit: %DO; text %END;  or one % statement."""
        i = self._skip_ws(toks, i)
        if i < len(toks) and toks[i][0] == "pct":
            j = self._skip_ws(toks, i + 1)
            if j < len(toks) and toks[j][0] == "name" \
                    and toks[j][1].upper() == "DO":
                k = self._skip_ws(toks, j + 1)
                if k < len(toks) and toks[k][1] == ";":
                    body, i2 = self._collect_group(toks, k + 1)
                    if out is not None:
                        out.extend(self._run(body))
                    return i2
            return self._pp_statement(toks, i + 1,
                                      out if out is not None else [])
        raise PreprocError("%THEN/%ELSE must be followed by a % statement "
                           "or %DO; ... %END;")

    def _collect_group(self, toks, i):
        """Collect tokens until the matching %END;"""
        body = []
        depth = 0
        while i < len(toks):
            if toks[i][0] == "pct":
                j = self._skip_ws(toks, i + 1)
                if j < len(toks) and toks[j][0] == "name":
                    w = toks[j][1].upper()
                    if w == "DO":
                        depth += 1
                    elif w == "END":
                        if depth == 0:
                            _, end = self._until_semi(toks, j + 1)
                            return body, end
                        depth -= 1
            body.append(toks[i])
            i += 1
        raise PreprocError("missing %END")

    def _pp_if(self, toks, i, out):
        cond = []
        while i < len(toks):
            if toks[i][0] == "pct":
                j = self._skip_ws(toks, i + 1)
                if j < len(toks) and toks[j][0] == "name" \
                        and toks[j][1].upper() == "THEN":
                    i = j + 1
                    break
            cond.append(toks[i])
            i += 1
        else:
            raise PreprocError("%IF without %THEN")
        truth = self._truthy(self._eval(
            [t for t in cond if t[0] not in ("ws", "comment")]))
        i = self._pp_unit(toks, i, out if truth else None)
        # optional %ELSE
        j = self._skip_ws(toks, i)
        if j < len(toks) and toks[j][0] == "pct":
            k = self._skip_ws(toks, j + 1)
            if k < len(toks) and toks[k][0] == "name" \
                    and toks[k][1].upper() == "ELSE":
                return self._pp_unit(toks, k + 1,
                                     None if truth else out)
        return i

    def _pp_do(self, toks, i, out):
        seg, i = self._until_semi(toks, i)
        seg = [t for t in seg if t[0] not in ("ws", "comment")]
        body, i = self._collect_group(toks, i)
        if not seg:                      # plain %DO; — just a group
            out.extend(self._run(body))
            return i
        if len(seg) < 3 or seg[0][0] != "name" or seg[1][1] != "=":
            raise PreprocError("bad %DO specification")
        var = seg[0][1].upper()
        rest = seg[2:]
        to_ix = next((k for k, t in enumerate(rest)
                      if t[0] == "name" and t[1].upper() == "TO"), None)
        if to_ix is None:
            raise PreprocError("%DO needs TO")
        by_ix = next((k for k, t in enumerate(rest)
                      if t[0] == "name" and t[1].upper() == "BY"), None)
        start = int(self._eval(rest[:to_ix]))
        if by_ix is not None:
            limit = int(self._eval(rest[to_ix + 1:by_ix]))
            step = int(self._eval(rest[by_ix + 1:]))
        else:
            limit = int(self._eval(rest[to_ix + 1:]))
            step = 1
        typ = self.vars.get(var, ("FIXED", 0))[0]
        v = start
        while (step >= 0 and v <= limit) or (step < 0 and v >= limit):
            self.vars[var] = (typ, v)
            self.active.add(var)
            out.extend(self._run(list(body)))
            v += step
        self.vars[var] = (typ, v)
        return i

    # ---- expressions -------------------------------------------------------

    def _eval(self, seg):
        self._toks = [t for t in seg if t[0] not in ("ws", "comment")]
        self._pos = 0
        v = self._expr_or()
        if self._pos != len(self._toks):
            raise PreprocError("bad preprocessor expression")
        return v

    def _peek(self):
        return self._toks[self._pos] if self._pos < len(self._toks) \
            else (None, None)

    def _next(self):
        t = self._peek()
        self._pos += 1
        return t

    def _truthy(self, v):
        if isinstance(v, str):
            return v.strip() not in ("", "0")
        return v != 0

    def _expr_or(self):
        v = self._expr_and()
        while self._peek()[1] == "|":
            self._next()
            r = self._expr_and()
            v = 1 if (self._truthy(v) or self._truthy(r)) else 0
        return v

    def _expr_and(self):
        v = self._expr_cmp()
        while self._peek()[1] == "&":
            self._next()
            r = self._expr_cmp()
            v = 1 if (self._truthy(v) and self._truthy(r)) else 0
        return v

    def _expr_cmp(self):
        v = self._expr_cat()
        ops = {"=": "==", "^=": "!=", "~=": "!=", "¬=": "!=", "<>": "!=",
               "<": "<", ">": ">", "<=": "<=", ">=": ">="}
        while self._peek()[1] in ops:
            op = self._next()[1]
            r = self._expr_cat()
            a, b = v, r
            if isinstance(a, str) or isinstance(b, str):
                a, b = str(a), str(b)
            res = {"=": a == b, "^=": a != b, "~=": a != b, "¬=": a != b,
                   "<>": a != b, "<": a < b, ">": a > b,
                   "<=": a <= b, ">=": a >= b}[op]
            v = 1 if res else 0
        return v

    def _expr_cat(self):
        v = self._expr_add()
        while self._peek()[1] == "||":
            self._next()
            v = str(v) + str(self._expr_add())
        return v

    def _expr_add(self):
        v = self._expr_mul()
        while self._peek()[1] in ("+", "-"):
            op = self._next()[1]
            r = self._expr_mul()
            v = int(v) + int(r) if op == "+" else int(v) - int(r)
        return v

    def _expr_mul(self):
        v = self._expr_atom()
        while self._peek()[1] in ("*", "/"):
            op = self._next()[1]
            r = self._expr_atom()
            v = int(v) * int(r) if op == "*" else int(v) // int(r)
        return v

    def _expr_atom(self):
        kind, val = self._next()
        if kind == "number":
            return int(val)
        if kind == "string":
            return val[1:-1].replace("''", "'")
        if kind == "name":
            name = val.upper()
            if name in self.vars:
                return self.vars[name][1]
            return name                 # bare names compare as text
        if val == "-":
            return -int(self._expr_atom())
        if val == "+":
            return int(self._expr_atom())
        if val in ("¬", "^", "~"):
            return 0 if self._truthy(self._expr_atom()) else 1
        if val == "(":
            v = self._expr_or()
            if self._next()[1] != ")":
                raise PreprocError("missing ) in preprocessor expression")
            return v
        raise PreprocError("bad token %r in preprocessor expression" % val)


def preprocess(text, include_dir="."):
    """Run the preprocessor if the source contains % statements."""
    if not re.search(r"^\s*%", text, re.MULTILINE):
        return text
    return Preprocessor(include_dir).process(text)
