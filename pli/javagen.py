"""PL/I -> Java transpiler (experimental, structured F-level subset).

Scope (v1): external and nested/recursive procedures, FIXED BINARY (as
Java long) / FLOAT (as double) / CHAR / BIT scalars and 1-based 1-D
arrays, IF/DO(WHILE|UNTIL|iterative)/SELECT, GOTO restricted to labels
at a procedure's own top level (goto-elimination via a switch/while
dispatch loop), LEAVE/ITERATE via labeled break/continue, CALL/RETURN,
PUT LIST/EDIT(A,F,X)/SKIP, GET LIST, string/bit/arithmetic builtins.

Deliberately NOT supported (raises CodegenError with a clear message
rather than silently mistranslating): structures, PICTURE, ON-conditions,
BASED/POINTER/CONTROLLED/DEFINED/UNSPEC, record I/O, COMPLEX, exact
FIXED DECIMAL, multitasking, the preprocessor is fine (it already runs
as a text pass before parsing), multi-dimensional arrays, non-1 array
lower bounds, labels/DCLs nested inside DO/IF/SELECT/BEGIN bodies.

Scalars are generated as 1-element Java arrays (long[1]/double[1]/
String[1]) so CALL-by-reference is just array aliasing; PL/I arrays
map straight to Java arrays. Nested PL/I procedures become non-static
Java inner classes, relying on Java's automatic implicit-outer-instance
capture to reproduce PL/I's static (lexical) scoping and to make
recursive self-calls and sibling calls "just work" with no special
casing at the call site.
"""
from . import nodes as N
from .fixeddec import FixedDec


class CodegenError(Exception):
    pass


# ---- small text helpers ----------------------------------------------------

def sanitize(name):
    return name.replace("#", "_H_").replace("@", "_A_")


def java_string_literal(s):
    out = ['"']
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def unlabel(stmt):
    while isinstance(stmt, N.Labeled):
        stmt = stmt.stmt
    return stmt


def const_int(node, what):
    if isinstance(node, N.Num) and isinstance(node.value, int):
        return node.value
    raise CodegenError("%s must be a constant integer literal for the "
                       "Java backend" % what)


class VarInfo:
    __slots__ = ("kind", "is_array", "size", "char_len", "is_bit", "varying")

    def __init__(self, kind, is_array=False, size=None, char_len=None,
                is_bit=False, varying=False):
        self.kind = kind          # 'long' | 'double' | 'string'
        self.is_array = is_array
        self.size = size          # element count, for local (non-param) arrays
        self.char_len = char_len  # constant CHAR(n) width, or None/"*"
        self.is_bit = is_bit
        self.varying = varying

    def jtype(self):
        # Every field is a Java array (single bracket level): PL/I scalars
        # are 1-element arrays (so CALL-by-reference is just aliasing);
        # PL/I arrays are already reference types and need no extra boxing.
        base = {"long": "long", "double": "double", "string": "String"}[self.kind]
        return base + "[]"


def resolve_attrs(attrs, name=None):
    """attrs: list of (kind, value) tuples from DeclItem/proc RETURNS."""
    base = None
    char_len = None
    is_bit = False
    varying = False
    for kind, val in attrs:
        if kind in ("FIXED", "BINKW"):
            base = "long"
        elif kind in ("FLOAT", "DECKW"):
            if base != "long":
                base = "double"
        elif kind == "CHARKW":
            base = "string"
            char_len = val
        elif kind == "BIT":
            base = "string"
            is_bit = True
            char_len = val
        elif kind == "VARYING":
            varying = True
        elif kind == "INIT":
            pass
        elif kind in ("STATIC", "AUTOMATIC"):
            pass
        elif kind == "LABEL":
            raise CodegenError("LABEL variables are not supported by the "
                               "Java backend")
        elif kind == "GENERIC":
            raise CodegenError("attribute %r is not supported by the Java "
                               "backend" % (val[0],))
        else:
            raise CodegenError("attribute %s is not supported by the Java "
                               "backend" % kind)
    # FIXED/FLOAT precision with fractional digits needs exact decimal,
    # which v1 does not implement; only checked when it matters (FIXED).
    for kind, val in attrs:
        if kind == "FIXED" and val and val[1]:
            raise CodegenError("FIXED DECIMAL(p,q) with q>0 needs exact "
                               "decimal arithmetic, not supported by the "
                               "Java backend v1")
    if base is None:
        base = "long" if (name and name[0] in "IJKLMN") else "double"
    return base, char_len, is_bit, varying


def resolve_char_len(char_len):
    """CHAR(n)/BIT(n) length -> constant int, or None for '*'/absent."""
    if char_len is None or char_len == "*":
        return None
    return const_int(char_len, "CHAR/BIT length")


def expand_init(items):
    """Mirror interpreter._expand_init: each item is ('VAL', expr) or
    ('REP', count, expr); REP's count must be a constant int in v1."""
    out = []
    for it in items:
        if isinstance(it, tuple) and it[0] == "REP":
            n = const_int(it[1], "INIT repeat count")
            out.extend([it[2]] * max(n, 0))
        elif isinstance(it, tuple) and it[0] == "VAL":
            out.append(it[1])
        else:
            out.append(it)  # legacy: bare expression node
    return out


def collect_declares(body):
    out = []
    for stmt in body:
        inner = unlabel(stmt)
        if isinstance(inner, N.ProcDef):
            continue
        if isinstance(inner, N.Declare):
            out.extend(inner.items)
        elif isinstance(inner, N.Prefix):
            out.extend(collect_declares([inner.stmt]))
    return out


def always_returns(stmt):
    """Conservative static check: does this statement provably never fall
    through? Used to decide whether the trailing safety net in invoke()
    would be unreachable (a javac error, not just dead code)."""
    inner = unlabel(stmt)
    if isinstance(inner, (N.Return, N.Stop)):
        return True
    if isinstance(inner, N.If):
        return (inner.els is not None and always_returns(inner.then)
                and always_returns(inner.els))
    if isinstance(inner, N.Block):
        real = [s for s in inner.body
                if not isinstance(unlabel(s), (N.Declare, N.ProcDef, N.Null))]
        return bool(real) and always_returns(real[-1])
    if isinstance(inner, N.Select):
        if inner.otherwise is None:
            return False
        return (all(always_returns(body) for _, body in inner.whens)
                and always_returns(inner.otherwise))
    return False


def body_always_returns(body):
    real = [s for s in body
           if not isinstance(unlabel(s), (N.Declare, N.ProcDef, N.Null))]
    return bool(real) and always_returns(real[-1])


def collect_nested_procs(body):
    out = []
    for stmt in body:
        if isinstance(stmt, N.Labeled) and isinstance(stmt.stmt, N.ProcDef):
            out.append((stmt.name, stmt.stmt))
    return out


class Ctx:
    """Per-procedure codegen state."""
    def __init__(self, syms, top_labels, dispatch_label):
        self.syms = syms                  # name -> VarInfo
        self.top_labels = top_labels      # name -> case index, or {}
        self.dispatch_label = dispatch_label
        self.loop_stack = []              # list of (pli_label_or_None, java_label)


class JavaCodeGen:
    def __init__(self):
        self.lines = []
        self.indent = 0
        self.ctx = None
        self.loop_counter = 0
        self.proc_seen = {}    # id(ProcDef) -> java class name, to dedupe

    # ---- line emission ----

    def w(self, text=""):
        self.lines.append(("    " * self.indent) + text if text else "")

    # ---- top-level entry ----

    def generate(self, ast, class_name):
        top_procs = []
        for stmt in ast:
            inner = unlabel(stmt)
            if isinstance(stmt, N.Labeled) and isinstance(inner, N.ProcDef):
                top_procs.append((stmt.name, inner))
            elif isinstance(inner, N.Null):
                continue
            else:
                raise CodegenError("line %d: only PROCEDURE definitions are "
                                   "allowed at top level for the Java "
                                   "backend" % getattr(stmt, "lineno", 0))
        if not top_procs:
            raise CodegenError("no PROCEDURE found")

        main_name = None
        for name, proc in top_procs:
            if "MAIN" in [o.upper() for o in proc.options]:
                main_name = name
        if main_name is None:
            if len(top_procs) == 1:
                main_name = top_procs[0][0]
            else:
                raise CodegenError(
                    "multiple top-level procedures and none has "
                    "OPTIONS(MAIN); add OPTIONS(MAIN) to the entry point")

        self.ret_kinds = {}
        for name, proc in top_procs:
            self._collect_ret_kinds(name, proc)

        self.lines = []  # no package; PLI.java compiles alongside, default pkg
        self.w("public final class %s {" % class_name)
        self.indent += 1
        self.w("public static void main(String[] args) {")
        self.indent += 1
        self.w("try {")
        self.indent += 1
        self.w("new %s().invoke();" % sanitize(main_name))
        self.indent -= 1
        self.w("} finally {")
        self.indent += 1
        self.w("PLI.flushLine();")
        self.indent -= 1
        self.w("}")
        self.indent -= 1
        self.w("}")
        self.w()
        for name, proc in top_procs:
            self.gen_proc_class(name, proc, is_static=True)
        self.indent -= 1
        self.w("}")
        return "\n".join(self.lines) + "\n"

    def _collect_ret_kinds(self, name, proc):
        """Pre-scan every procedure (top-level and nested) for its return
        kind, so a CALL used as an expression elsewhere knows the right
        Java type before that procedure's own class is generated."""
        if proc.returns:
            ret_kind, _, _, _ = resolve_attrs(proc.returns)
        else:
            ret_kind = None
        self.ret_kinds[name] = ret_kind
        for nname, nproc in collect_nested_procs(proc.body):
            self._collect_ret_kinds(nname, nproc)

    # ---- one procedure -> one (possibly nested) class ----

    def gen_proc_class(self, pli_name, proc, is_static):
        jname = sanitize(pli_name)
        declares = collect_declares(proc.body)
        nested = collect_nested_procs(proc.body)

        syms = {}
        param_infos = []
        for pname in proc.params:
            item = next((d for d in declares
                        if pname in d.names), None)
            if item is None:
                raise CodegenError("parameter %s of %s has no DECLARE "
                                   "(required by the Java backend)"
                                   % (pname, pli_name))
            info = self._var_info(item)
            syms[pname] = info
            param_infos.append((pname, info))

        local_items = [d for d in declares if not any(
            n in [p for p, _ in param_infos] for n in d.names)]
        for item in local_items:
            for nm in item.names:
                syms[nm] = self._var_info(item)

        ret_kind = None
        if proc.returns:
            ret_kind, _, ret_bit, _ = resolve_attrs(proc.returns)

        mods = "static final" if is_static else "final"
        self.w("%s class %s {" % (mods, jname))
        self.indent += 1

        # fields: parameters (declared, bound in invoke()) then locals
        for pname, info in param_infos:
            self.w("private %s %s;" % (info.jtype(), sanitize(pname)))
        for item in local_items:
            self._emit_field(item, syms)
        if param_infos or local_items:
            self.w()

        # nested procedures declared in this body
        for nname, nproc in nested:
            self.gen_proc_class(nname, nproc, is_static=False)

        # invoke()
        jret = {"long": "long", "double": "double", "string": "String",
               None: "void"}[ret_kind]
        params_sig = ", ".join(
            "%s p_%s" % (syms[p].jtype(), sanitize(p)) for p, _ in param_infos)
        self.w("%s invoke(%s) {" % (jret, params_sig))
        self.indent += 1
        for pname, _ in param_infos:
            self.w("this.%s = p_%s;" % (sanitize(pname), sanitize(pname)))

        old_ctx = self.ctx
        self.loop_counter += 1
        dispatch_label = "dispatch%d" % self.loop_counter
        self.ctx = Ctx(syms, {}, dispatch_label)
        self.ctx.ret_kind = ret_kind
        self._gen_proc_body(proc.body)
        self.ctx = old_ctx

        # The dispatch (GOTO) loop always has a reachable `break;` after its
        # switch, so the trailing safety net stays genuinely reachable and
        # must be kept; only the label-free straight-line case can prove
        # (conservatively) that control never falls through, in which case
        # javac itself would flag the safety net as dead code.
        segs = self._flatten_segments(proc.body)
        has_dispatch = any(lbl is not None for lbl, _ in segs)
        skip_throw = (not has_dispatch) and body_always_returns(proc.body)
        if jret != "void" and not skip_throw:
            self.w('throw new RuntimeException("%s: fell off end without '
                   'RETURN");' % jname)
        self.indent -= 1
        self.w("}")
        self.indent -= 1
        self.w("}")
        self.w()

    def _var_info(self, item):
        base, char_len, is_bit, varying = resolve_attrs(item.attrs, item.names[0])
        if item.dims is not None:
            if len(item.dims) != 1:
                raise CodegenError("multi-dimensional arrays are not "
                                   "supported by the Java backend")
            b = item.dims[0]
            if b == ("*",):
                return VarInfo(base, is_array=True)  # assumed-size param
            lo, hi = b
            if lo is not None:
                lo_val = const_int(lo, "array lower bound")
                if lo_val != 1:
                    raise CodegenError("only 1-based array bounds are "
                                       "supported by the Java backend")
            size = const_int(hi, "array upper bound")
            return VarInfo(base, is_array=True, size=size)
        cl = resolve_char_len(char_len)
        return VarInfo(base, char_len=cl, is_bit=is_bit, varying=varying)

    def _emit_field(self, item, syms):
        info = syms[item.names[0]]
        raw_init = next((v for k, v in item.attrs if k == "INIT"), None)
        init_vals = expand_init(raw_init) if raw_init is not None else None
        for nm in item.names:
            jt = info.jtype()
            if info.is_array:
                if info.size is None:
                    raise CodegenError("array %s: assumed-size arrays are "
                                       "only valid as parameters" % nm)
                elem = info.jtype()[:-2]
                if init_vals is not None:
                    vals = [self._const_literal(v, info) for v in init_vals]
                    while len(vals) < info.size:
                        vals.append(self._default_literal(info))
                    self.w("private %s %s = {%s};"
                          % (jt, sanitize(nm), ", ".join(vals)))
                else:
                    self.w("private %s %s = new %s[%d];"
                          % (jt, sanitize(nm), elem, info.size))
            else:
                if init_vals is not None:
                    val = self._const_literal(init_vals[0], info)
                else:
                    val = self._default_literal(info)
                self.w("private %s %s = {%s};" % (jt, sanitize(nm), val))

    def _default_literal(self, info):
        return {"long": "0L", "double": "0.0", "string": '""'}[info.kind]

    def _const_literal(self, expr_node, info):
        text, kind = self.expr(expr_node)
        return text

    # ---- statement bodies ----

    def _gen_proc_body(self, body):
        segments = self._flatten_segments(body)
        real_labels = [seg[0] for seg in segments if seg[0] is not None]
        if not real_labels:
            for _, stmts in segments:
                self.gen_stmt_list(stmts)
            return
        for i, (label, _) in enumerate(segments):
            if label is not None:
                self.ctx.top_labels[label] = i
        self.w("int pc = 0;")
        self.w("%s: while (true) {" % self.ctx.dispatch_label)
        self.indent += 1
        self.w("switch (pc) {")
        self.indent += 1
        for i, (label, stmts) in enumerate(segments):
            self.w("case %d: {" % i)
            self.indent += 1
            if label:
                self.w("// label %s" % label)
            self.gen_stmt_list(stmts)
            self.w("}")
            self.indent -= 1
        self.indent -= 1
        self.w("}")
        self.w("break;")
        self.indent -= 1
        self.w("}")

    def _flatten_segments(self, body):
        segments = [[None, []]]
        for stmt in body:
            label = None
            inner = stmt
            if isinstance(stmt, N.Labeled):
                label = stmt.name
                inner = stmt.stmt
            if isinstance(inner, (N.Declare, N.ProcDef)):
                continue
            if label is not None:
                segments.append([label, []])
            if isinstance(inner, N.Null):
                continue
            segments[-1][1].append(inner)
        return segments

    def gen_stmt_list(self, stmts):
        for s in stmts:
            self.gen_stmt(s)

    def gen_stmt(self, stmt):
        if isinstance(stmt, N.Labeled):
            raise CodegenError(
                "line %d: label %r is nested inside a block; the Java "
                "backend only supports labels at a procedure's own top "
                "level" % (stmt.lineno, stmt.name))
        method = getattr(self, "gen_" + stmt.kind, None)
        if method is None:
            raise CodegenError("line %d: %s is not supported by the Java "
                               "backend" % (getattr(stmt, "lineno", 0),
                                           stmt.kind))
        method(stmt)

    def gen_Null(self, stmt):
        pass

    def gen_Declare(self, stmt):
        pass  # handled as fields

    def gen_Assign(self, stmt):
        target = stmt.target
        if isinstance(target, N.Ref) and target.name == "SUBSTR" and target.args:
            base_ref = target.args[0]
            if not isinstance(base_ref, N.Ref) or base_ref.args is not None:
                raise CodegenError("line %d: SUBSTR pseudo-variable target "
                                   "must be a plain scalar variable"
                                   % stmt.lineno)
            info = self._sym(base_ref.name, stmt.lineno)
            i_text, _ = self.expr(target.args[1])
            if len(target.args) > 2:
                len_text, _ = self.expr(target.args[2])
            else:
                len_text = "(%s[0].length() - (%s) + 1)" % (
                    sanitize(base_ref.name), i_text)
            val_text, _ = self.expr(stmt.value)
            jn = sanitize(base_ref.name)
            self.w("%s[0] = PLI.substrAssign(%s[0], %s, %s, %s);"
                  % (jn, jn, i_text, len_text, val_text))
            return
        val_text, val_kind = self.expr(stmt.value)
        self._gen_store(target, val_text, val_kind, stmt.lineno)

    def _gen_store(self, target, val_text, val_kind, lineno):
        if not isinstance(target, N.Ref):
            raise CodegenError("line %d: unsupported assignment target"
                               % lineno)
        info = self._sym(target.name, lineno)
        jn = sanitize(target.name)
        if target.args is not None:
            if not info.is_array:
                raise CodegenError("line %d: %s is not an array"
                                   % (lineno, target.name))
            sub, _ = self.expr(target.args[0])
            lhs = "%s[(int)(%s) - 1]" % (jn, sub)
        else:
            if info.is_array:
                raise CodegenError("line %d: %s is an array, aggregate "
                                   "assignment not supported by the Java "
                                   "backend" % (lineno, target.name))
            lhs = "%s[0]" % jn
        text = val_text
        if info.kind == "string" and not info.is_bit and not info.varying \
                and info.char_len:
            text = "PLI.charFixed(%s, %d)" % (val_text, info.char_len)
        self.w("%s = %s;" % (lhs, text))

    def gen_CallStmt(self, stmt):
        call_text = self._gen_call(stmt.name, stmt.args, stmt.lineno)
        self.w("%s;" % call_text)

    def _gen_call(self, name, args, lineno):
        jname = sanitize(name)
        arg_texts = []
        for a in args:
            arg_texts.append(self._arg_expr(a))
        return "new %s(%s).invoke(%s)" % (jname, "", ", ".join(arg_texts))

    def _arg_expr(self, node):
        if isinstance(node, N.Ref) and node.args is None \
                and node.name in self.ctx.syms:
            return sanitize(node.name)
        text, kind = self.expr(node)
        java_elem = {"long": "long", "double": "double", "string": "String",
                    "bitstring": "String"}[kind]
        return "new %s[]{%s}" % (java_elem, text)

    def gen_If(self, stmt):
        cond, kind = self.expr(stmt.cond)
        self.w("if (%s) {" % self._as_bool(cond, kind))
        self.indent += 1
        self.gen_stmt(stmt.then)
        self.indent -= 1
        if stmt.els is not None:
            self.w("} else {")
            self.indent += 1
            self.gen_stmt(stmt.els)
            self.indent -= 1
        self.w("}")

    def gen_Block(self, stmt):
        for s in stmt.body:
            self.gen_stmt(s)

    def gen_DoWhile(self, stmt, label=None):
        whiles = [e for k, e in stmt.conds if k == "WHILE"]
        untils = [e for k, e in stmt.conds if k == "UNTIL"]
        if whiles and untils:
            raise CodegenError("line %d: combined DO WHILE/UNTIL is not "
                               "supported by the Java backend" % stmt.lineno)
        jlabel = self._push_loop(label)
        if untils:
            cond, kind = self.expr(untils[0])
            self.w("%s: do {" % jlabel)
            self.indent += 1
            self.gen_stmt_list(stmt.body)
            self.indent -= 1
            self.w("} while (!(%s));" % self._as_bool(cond, kind))
        else:
            cond, kind = self.expr(whiles[0]) if whiles else ("true", "long")
            self.w("%s: while (%s) {" % (jlabel, self._as_bool(cond, kind)
                                         if whiles else "true"))
            self.indent += 1
            self.gen_stmt_list(stmt.body)
            self.indent -= 1
            self.w("}")
        self._pop_loop()

    def gen_DoIter(self, stmt, label=None):
        if len(stmt.specs) != 1:
            raise CodegenError("line %d: multiple DO specifications are "
                               "not supported by the Java backend"
                               % stmt.lineno)
        spec = stmt.specs[0]
        if spec.conds:
            raise CodegenError("line %d: DO ... TO ... WHILE/UNTIL is not "
                               "supported by the Java backend" % stmt.lineno)
        info = self._sym(stmt.var, stmt.lineno)
        jv = sanitize(stmt.var)
        start, _ = self.expr(spec.start)
        by_sign = 1
        by_text = "1"
        if spec.by is not None:
            if isinstance(spec.by, N.Num) and isinstance(spec.by.value, (int, float)):
                by_sign = 1 if spec.by.value >= 0 else -1
                by_text, _ = self.expr(spec.by)
            elif isinstance(spec.by, N.UnOp) and spec.by.op == "MINUS" \
                    and isinstance(spec.by.operand, N.Num):
                by_sign = -1
                by_text, _ = self.expr(spec.by)
            else:
                raise CodegenError("line %d: DO BY must be a constant "
                                   "literal for the Java backend"
                                   % stmt.lineno)
        jlabel = self._push_loop(label)
        self.w("%s[0] = %s;" % (jv, start))
        if spec.to is not None:
            to, _ = self.expr(spec.to)
            op = "<=" if by_sign >= 0 else ">="
            self.w("%s: for (; %s[0] %s (%s); %s[0] += %s) {"
                  % (jlabel, jv, op, to, jv, by_text))
        else:
            self.w("%s: for (;;) {" % jlabel)
        self.indent += 1
        self.gen_stmt_list(stmt.body)
        self.indent -= 1
        self.w("}")
        self._pop_loop()

    def _push_loop(self, pli_label):
        self.loop_counter += 1
        jlabel = "loop%d" % self.loop_counter
        self.ctx.loop_stack.append((pli_label, jlabel))
        return jlabel

    def _pop_loop(self):
        self.ctx.loop_stack.pop()

    def gen_Select(self, stmt):
        subject_text = subject_kind = None
        if stmt.subject is not None:
            subject_text, subject_kind = self.expr(stmt.subject)
        first = True
        for exprs, body in stmt.whens:
            conds = []
            for e in exprs:
                text, kind = self.expr(e)
                if subject_text is None:
                    conds.append(self._as_bool(text, kind))
                else:
                    conds.append(self._eq(subject_text, subject_kind, text, kind))
            kw = "if" if first else "} else if"
            self.w("%s (%s) {" % (kw, " || ".join(conds)))
            self.indent += 1
            self.gen_stmt(body)
            self.indent -= 1
            first = False
        if stmt.otherwise is not None:
            self.w("} else {")
            self.indent += 1
            self.gen_stmt(stmt.otherwise)
            self.indent -= 1
        else:
            self.w("} else {")
            self.indent += 1
            self.w('throw new PLI.PLIError("ERROR", '
                  '"SELECT: no WHEN matched and no OTHERWISE");')
            self.indent -= 1
        self.w("}")

    def gen_Goto(self, stmt):
        if stmt.label not in self.ctx.top_labels:
            raise CodegenError(
                "line %d: GOTO %s: target is not a top-level label of "
                "this procedure (jumps into nested blocks are not "
                "supported by the Java backend)" % (stmt.lineno, stmt.label))
        self.w("pc = %d; continue %s;"
              % (self.ctx.top_labels[stmt.label], self.ctx.dispatch_label))

    def gen_Return(self, stmt):
        if stmt.value is None:
            self.w("return;")
        else:
            text, kind = self.expr(stmt.value)
            self.w("return %s;" % text)

    def gen_Stop(self, stmt):
        self.w("System.exit(0);")

    def gen_Leave(self, stmt):
        jlabel = self._find_loop(stmt.label)
        self.w("break %s;" % jlabel)

    def gen_Iterate(self, stmt):
        jlabel = self._find_loop(stmt.label)
        self.w("continue %s;" % jlabel)

    def _find_loop(self, pli_label):
        if pli_label is None:
            if not self.ctx.loop_stack:
                raise CodegenError("LEAVE/ITERATE outside any loop")
            return self.ctx.loop_stack[-1][1]
        for lbl, jlabel in reversed(self.ctx.loop_stack):
            if lbl == pli_label:
                return jlabel
        raise CodegenError("LEAVE/ITERATE %s: no such enclosing loop label"
                           % pli_label)

    def gen_Put(self, stmt):
        for clause in stmt.clauses:
            kind = clause[0]
            if kind == "PAGE":
                self.w("PLI.skip();")
            elif kind == "SKIP":
                if clause[1] is not None:
                    n, _ = self.expr(clause[1])
                    self.w("PLI.skip(%s);" % n)
                else:
                    self.w("PLI.skip();")
            elif kind == "LIST":
                args = []
                for e in clause[1]:
                    text, k = self.expr(e)
                    if k == "bitstring":
                        text = "PLI.asBits(%s)" % text
                    args.append(text)
                self.w("PLI.putList(%s);" % ", ".join(args))
            elif kind == "EDIT":
                self._gen_put_edit(clause[1], clause[2])
            else:
                raise CodegenError("PUT %s is not supported by the Java "
                                   "backend" % kind)

    def _gen_put_edit(self, items, formats):
        fi = 0
        for item in items:
            f = formats[fi % len(formats)]
            name = f.name.upper()
            args = [self.expr(a)[0] for a in f.args]
            text, kind = self.expr(item)
            if name == "A":
                self.w("PLI.editA(%s%s);"
                      % (text, ", " + args[0] if args else ""))
            elif name == "F":
                if len(args) == 2:
                    self.w("PLI.editF(%s, %s, %s);" % (text, args[0], args[1]))
                elif len(args) == 1:
                    self.w("PLI.editF(%s, %s);" % (text, args[0]))
                else:
                    self.w("PLI.editF(%s);" % text)
            elif name == "X":
                self.w("PLI.editX(%s);" % args[0])
            else:
                raise CodegenError("PUT EDIT format item %s is not "
                                   "supported by the Java backend" % name)
            fi += 1

    def gen_Get(self, stmt):
        for clause in stmt.clauses:
            if clause[0] != "LIST":
                raise CodegenError("GET %s is not supported by the Java "
                                   "backend" % clause[0])
            for ref in clause[1]:
                if not isinstance(ref, N.Ref) or ref.args is not None:
                    raise CodegenError("GET LIST target must be a plain "
                                       "scalar variable")
                info = self._sym(ref.name, stmt.lineno)
                jn = sanitize(ref.name)
                getter = {"long": "getLong", "double": "getDouble",
                         "string": "getString"}[info.kind]
                self.w("%s[0] = PLI.%s();" % (jn, getter))

    # ---- expressions: returns (java_text, kind) ----

    def expr(self, node):
        method = getattr(self, "eval_" + node.kind, None)
        if method is None:
            raise CodegenError("expression %s is not supported by the "
                               "Java backend" % node.kind)
        return method(node)

    def eval_Num(self, node):
        v = node.value
        if isinstance(v, FixedDec):
            return (repr(float(v)), "double")
        if isinstance(v, int):
            return ("%dL" % v, "long")
        return (repr(float(v)), "double")

    def eval_Str(self, node):
        return (java_string_literal(node.value), "string")

    def eval_Bits(self, node):
        return (java_string_literal(node.value), "bitstring")

    @staticmethod
    def _ref_kind(info):
        return "bitstring" if info.is_bit else info.kind

    def eval_Ref(self, node):
        name = node.name
        info = self.ctx.syms.get(name)
        if info is not None:
            jn = sanitize(name)
            k = self._ref_kind(info)
            if node.args is not None:
                if not info.is_array:
                    raise CodegenError("%s is not an array" % name)
                sub, _ = self.expr(node.args[0])
                return ("%s[(int)(%s) - 1]" % (jn, sub), k)
            if info.is_array:
                return (jn, k + "[]")
            return ("%s[0]" % jn, k)
        if node.args is not None:
            return self._builtin_or_call(name, node.args)
        raise CodegenError("%s is not declared (the Java backend has no "
                           "implicit-declaration fallback)" % name)

    def _sym(self, name, lineno):
        info = self.ctx.syms.get(name)
        if info is None:
            raise CodegenError("line %d: %s is not declared" % (lineno, name))
        return info

    _BUILTIN_MAP = {
        "SUBSTR": ("substr", "string"),
        "LENGTH": ("length", "long"),
        "INDEX": ("index", "long"),
        "TRIM": ("trim", "string"),
        "LOWERCASE": ("lower", "string"),
        "UPPERCASE": ("upper", "string"),
        "ABS": ("abs", None),
        "MOD": ("mod", "long"),
        "MIN": ("min", None),
        "MAX": ("max", None),
        "SQRT": ("sqrt", "double"),
        "TRUNC": ("trunc", "long"),
        "CEIL": ("ceil", "long"),
        "FLOOR": ("floor", "long"),
        "ROUND": ("round", "long"),
        "REPEAT": ("repeatPlus", "string"),
        "COPY": ("repeatStr", "string"),
        "VERIFY": ("verify", "long"),
        "TRANSLATE": ("translate", "string"),
    }

    def _builtin_or_call(self, name, args):
        if name in ("HBOUND", "LBOUND", "DIM"):
            ref = args[0]
            if not isinstance(ref, N.Ref):
                raise CodegenError("%s requires a plain array argument" % name)
            info = self._sym(ref.name, 0)
            if not info.is_array:
                raise CodegenError("%s: %s is not an array" % (name, ref.name))
            jn = sanitize(ref.name)
            if name == "LBOUND":
                return ("1L", "long")
            return ("PLI.hbound(%s)" % jn, "long")
        arg_texts = [self.expr(a) for a in args]
        if name in self._BUILTIN_MAP:
            jfunc, forced_kind = self._BUILTIN_MAP[name]
            texts = [t for t, _ in arg_texts]
            kind = forced_kind or arg_texts[0][1]
            return ("PLI.%s(%s)" % (jfunc, ", ".join(texts)), kind)
        # otherwise: a procedure call used as an expression
        if name not in self.ret_kinds:
            raise CodegenError("%s is not declared and is not a known "
                               "procedure or builtin" % name)
        if self.ret_kinds[name] is None:
            raise CodegenError("%s is a void procedure and cannot be used "
                               "in an expression" % name)
        arg_strs = [self._arg_expr(a) for a in args]
        return ("new %s().invoke(%s)" % (sanitize(name), ", ".join(arg_strs)),
               self.ret_kinds[name])

    def eval_UnOp(self, node):
        text, kind = self.expr(node.operand)
        if node.op == "MINUS":
            return ("(-(%s))" % text, kind)
        if node.op == "PLUS":
            return (text, kind)
        if node.op == "NOT":
            if kind == "bitstring":
                return ("PLI.bitNot(%s)" % text, "bitstring")
            return ("(!(%s))" % self._as_bool(text, kind), "boolean")
        raise CodegenError("unary operator %s not supported" % node.op)

    def eval_BinOp(self, node):
        op = node.op
        ltext, lkind = self.expr(node.left)
        if op in ("AND", "OR"):
            rtext, rkind = self.expr(node.right)
            if lkind == "bitstring" and rkind == "bitstring":
                jfunc = "bitAnd" if op == "AND" else "bitOr"
                return ("PLI.%s(%s, %s)" % (jfunc, ltext, rtext), "bitstring")
            lb = self._as_bool(ltext, lkind)
            rb = self._as_bool(rtext, rkind)
            jop = "&&" if op == "AND" else "||"
            return ("(%s %s %s)" % (lb, jop, rb), "boolean")
        rtext, rkind = self.expr(node.right)
        if op == "CONCAT":
            kind = "bitstring" if lkind == rkind == "bitstring" else "string"
            return ("PLI.concat(%s, %s)" % (ltext, rtext), kind)
        if op in ("EQ", "NE", "LT", "LE", "GT", "GE"):
            return (self._compare(op, ltext, lkind, rtext, rkind), "boolean")
        if lkind in ("string", "bitstring") or rkind in ("string", "bitstring"):
            raise CodegenError("operator %s not supported on CHAR/BIT "
                               "operands" % op)
        if op == "PLUS":
            return ("(%s + %s)" % (ltext, rtext), self._num_kind(lkind, rkind))
        if op == "MINUS":
            return ("(%s - %s)" % (ltext, rtext), self._num_kind(lkind, rkind))
        if op == "STAR":
            return ("(%s * %s)" % (ltext, rtext), self._num_kind(lkind, rkind))
        if op == "SLASH":
            return ("PLI.zdiv((double)(%s), (double)(%s))" % (ltext, rtext),
                   "double")
        if op == "POW":
            return ("Math.pow((double)(%s), (double)(%s))" % (ltext, rtext),
                   "double")
        raise CodegenError("operator %s not supported" % op)

    def _num_kind(self, a, b):
        return "double" if "double" in (a, b) else "long"

    def _compare(self, op, ltext, lkind, rtext, rkind):
        if lkind == "bitstring" or rkind == "bitstring":
            if op == "EQ":
                return "(%s).equals(%s)" % (ltext, rtext)
            if op == "NE":
                return "(!(%s).equals(%s))" % (ltext, rtext)
            raise CodegenError("ordering comparisons (< <= > >=) on BIT "
                               "strings are not supported by the Java "
                               "backend")
        if lkind == "string" or rkind == "string":
            if op == "EQ":
                return "PLI.charEq(%s, %s)" % (ltext, rtext)
            if op == "NE":
                return "(!PLI.charEq(%s, %s))" % (ltext, rtext)
            jop = {"LT": "<", "LE": "<=", "GT": ">", "GE": ">="}[op]
            return "(PLI.charCmp(%s, %s) %s 0)" % (ltext, rtext, jop)
        jop = {"EQ": "==", "NE": "!=", "LT": "<", "LE": "<=",
              "GT": ">", "GE": ">="}[op]
        return "(%s %s %s)" % (ltext, jop, rtext)

    def _eq(self, ltext, lkind, rtext, rkind):
        return self._compare("EQ", ltext, lkind, rtext, rkind)

    def _as_bool(self, text, kind):
        if kind == "boolean":
            return text
        if kind in ("string", "bitstring"):
            return "PLI.bitTrue(%s)" % text
        return "((%s) != 0)" % text


def generate(ast, class_name):
    return JavaCodeGen().generate(ast, class_name)
