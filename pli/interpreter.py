"""Tree-walking interpreter for the PL/I subset AST.

Semantics implemented:
- static (lexical) scoping with nested procedures and BEGIN blocks
- by-reference argument passing for plain variable / array-element
  arguments, dummy arguments for expressions (as in real PL/I)
- FIXED / FLOAT / CHAR(n) [VARYING] / BIT(n) conversion on assignment
- implicit declaration (I-N default FIXED BINARY, else FLOAT DECIMAL)
- arrays with arbitrary lower:upper bounds, aggregate scalar assignment
- GOTO to labels in any enclosing active block, LEAVE/ITERATE,
  RETURN(expr), STOP
- PUT LIST / PUT EDIT (A, B, F, E, X, COL, SKIP formats) / GET LIST
- ~35 builtin functions and the SUBSTR pseudo-variable
"""
import math
import os
import sys
import threading
import time

from . import nodes as N
from .parser import PLIParser, ParseError  # noqa: F401
from .picture import Picture, PicStr, PictureError
from .fixeddec import FixedDec, FixedOverflow, SizeError


class PLIError(Exception):
    pass


class BitStr(str):
    """A PL/I bit string: '0'/'1' characters."""
    def __repr__(self):
        return "'%s'B" % str.__str__(self)


# ---- control-flow signals -------------------------------------------------

class GotoSignal(Exception):
    def __init__(self, label):
        self.label = label


class ReturnSignal(Exception):
    def __init__(self, value):
        self.value = value


class StopSignal(Exception):
    pass


class LeaveSignal(Exception):
    def __init__(self, label):
        self.label = label


class IterateSignal(Exception):
    def __init__(self, label):
        self.label = label


# ---- conditions -------------------------------------------------------------

CONDITION_CODES = {
    "FINISH": 4, "ERROR": 9, "KEY": 50, "ENDFILE": 70, "ENDPAGE": 90,
    "OVERFLOW": 300, "FIXEDOVERFLOW": 310, "ZERODIVIDE": 320,
    "UNDERFLOW": 330, "SIZE": 340, "STRINGRANGE": 350,
    "CONDITION": 500, "SUBSCRIPTRANGE": 520, "CONVERSION": 612,
    "UNDEFINEDFILE": 80, "RECORD": 20, "TRANSMIT": 40,
}


class PLICondition(Exception):
    """A PL/I condition (catchable via ON; default action if unhandled)."""
    def __init__(self, name, msg="", qual=None, char="", source=""):
        self.name = name.upper()
        self.qual = qual
        self.msg = msg
        self.char = char          # ONCHAR for CONVERSION
        self.source = source      # ONSOURCE for CONVERSION
        self.code = CONDITION_CODES.get(self.name, 500)
        super().__init__(msg or self.name)


class LabelValue:
    """Runtime value of a label constant / LABEL variable."""
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return "LABEL(%s)" % self.name


class LabelConst:
    """A statement label registered in the environment."""
    def __init__(self, name):
        self.name = name


class PLIStructure:
    """A structure: ordered members (Variable / PLIArray / PLIStructure)."""
    def __init__(self, name):
        self.name = name
        self.members = {}         # insertion-ordered
        self.spec = None          # DeclItem list, kept for LIKE

    def find(self, name):
        """Find a member by (possibly partial) qualification."""
        if name in self.members:
            return self.members[name]
        hits = []
        for m in self.members.values():
            if isinstance(m, PLIStructure):
                hit = m.find(name)
                if hit is not None:
                    hits.append(hit)
        if len(hits) > 1:
            raise PLIError("ambiguous reference %s in structure %s"
                           % (name, self.name))
        return hits[0] if hits else None

    def leaves(self):
        for m in self.members.values():
            if isinstance(m, PLIStructure):
                yield from m.leaves()
            else:
                yield m

    def leaf_values(self):
        for entry in self.leaves():
            if isinstance(entry, PLIArray):
                yield from entry.data
            else:
                yield entry.value


# ---- runtime objects --------------------------------------------------------

class Decl:
    """Resolved declaration attributes."""
    def __init__(self, base="FLOAT", length=None, varying=False, prec=None,
                 pic=None):
        self.base = base          # FIXED | FLOAT | CHAR | BIT | LABEL | PIC
        self.length = length      # for CHAR/BIT
        self.varying = varying
        self.prec = prec          # (p, q) for FIXED/FLOAT
        self.pic = pic            # Picture object for base == "PIC"

    @staticmethod
    def default_for(name):
        # PL/I implicit rules: names starting I..N are FIXED BINARY
        return Decl("FIXED") if name[0] in "IJKLMN" else Decl("FLOAT")


class Variable:
    """A storage box; parameters may share it (by-reference)."""
    __slots__ = ("value", "decl")

    def __init__(self, value, decl):
        self.value = value
        self.decl = decl


class PLIArray:
    def __init__(self, bounds, decl):
        self.bounds = bounds      # [(lo, hi), ...]
        self.decl = decl
        n = 1
        for lo, hi in bounds:
            n *= (hi - lo + 1)
        self.data = [default_value(decl) for _ in range(n)]

    def _offset(self, subs):
        if len(subs) != len(self.bounds):
            raise PLIError("wrong number of subscripts (%d for %d)"
                           % (len(subs), len(self.bounds)))
        off = 0
        for (lo, hi), s in zip(self.bounds, subs):
            i = int(to_number(s))
            if i < lo or i > hi:
                raise PLICondition("SUBSCRIPTRANGE",
                                   "subscript %d out of range %d:%d"
                                   % (i, lo, hi))
            off = off * (hi - lo + 1) + (i - lo)
        return off

    def get(self, subs):
        return self.data[self._offset(subs)]

    def set(self, subs, value):
        self.data[self._offset(subs)] = convert(value, self.decl)


class Procedure:
    def __init__(self, name, node, env):
        self.name = name
        self.node = node          # ProcDef
        self.env = env            # defining environment (static scoping)


class FormatDef:
    """A named FORMAT statement, referenced by the R format item."""
    def __init__(self, items):
        self.items = items


class EventValue:
    """An EVENT variable: completion flag + status, backed by a
    threading.Event for WAIT."""
    def __init__(self):
        self.ev = threading.Event()
        self.status = 0

    @property
    def complete(self):
        return self.ev.is_set()

    def __repr__(self):
        return "EVENT(%s)" % ("complete" if self.complete else "incomplete")


class Pointer:
    """A POINTER value: a reference to an allocated entry (or NULL)."""
    __slots__ = ("target",)

    def __init__(self, target=None):
        self.target = target

    def __eq__(self, other):
        return isinstance(other, Pointer) and self.target is other.target

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return id(self.target)

    def __repr__(self):
        return "NULL" if self.target is None else "PTR(%#x)" % id(self.target)


class BasedVar:
    """A BASED variable: a template instantiated by ALLOCATE, referenced
    through its declared pointer (or explicit P-> qualification)."""
    def __init__(self, name, item, subitems, ptr_ref):
        self.name = name
        self.item = item          # DeclItem (level-1 for structures)
        self.subitems = subitems  # [] for scalars, member items for structs
        self.ptr_ref = ptr_ref    # AST of the BASED(P) pointer, or None


class Controlled:
    """A CONTROLLED variable: a stack of allocations."""
    def __init__(self, name, item, subitems=None):
        self.name = name
        self.item = item
        self.subitems = subitems or []
        self.stack = []


class DefinedVar:
    """A DEFINED overlay onto another variable (string overlay model)."""
    def __init__(self, name, decl, base_ref, position):
        self.name = name
        self.decl = decl
        self.base_ref = base_ref  # AST ref of the base variable
        self.position = position  # 1-based POSITION, default 1


class PLIFile:
    """A PL/I file.  CONSECUTIVE files are text files (one record per
    line); INDEXED files are key->record maps persisted as tab-separated
    lines.  STREAM files carry their own GET/PUT buffers."""
    def __init__(self, name):
        self.name = name
        self.stream = False       # STREAM vs RECORD
        self.mode = None          # INPUT | OUTPUT | UPDATE
        self.keyed = False
        self.indexed = False
        self.print_file = False
        self.title = None
        self.handle = None
        self.index = None         # dict for INDEXED
        self.seq_keys = None      # sequential read position for INDEXED
        self.dirty = False
        self.column = 0           # PUT column for stream output
        self.tokens = []          # GET LIST buffer
        self.in_line = ""         # GET EDIT buffer
        self.in_pos = 0

    @property
    def is_open(self):
        return self.handle is not None or self.index is not None


class Environment:
    def __init__(self, parent=None):
        self.vars = {}
        self.parent = parent

    def lookup(self, name):
        env = self
        while env is not None:
            if name in env.vars:
                return env.vars[name]
            env = env.parent
        return None

    def declare(self, name, entry):
        self.vars[name] = entry


# ---- value helpers -----------------------------------------------------------

def default_value(decl):
    if decl.base == "FIXED":
        return 0
    if decl.base == "FLOAT":
        return 0.0
    if decl.base == "CHAR":
        return "" if decl.varying else " " * (decl.length or 0)
    if decl.base == "BIT":
        return BitStr("0" * (decl.length or 0))
    if decl.base == "LABEL":
        return None
    if decl.base == "POINTER":
        return Pointer(None)
    if decl.base == "COMPLEX":
        return 0j
    if decl.base == "EVENT":
        return EventValue()
    if decl.base == "PIC":
        return (" " * decl.pic.length if decl.pic.is_char
                else decl.pic.edit(0))
    return 0


def to_number(v):
    if isinstance(v, PicStr):
        return v.num
    if isinstance(v, BitStr):
        return int(v, 2) if v else 0
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float, complex, FixedDec)):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return 0
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                bad = next((c for c in s if c not in "0123456789+-.E"), s[0])
                raise PLICondition("CONVERSION",
                                   "cannot convert %r to a number" % v,
                                   char=bad, source=v)
    raise PLIError("cannot convert %r to a number" % (v,))


def to_string(v):
    if isinstance(v, BitStr):
        return str(v)
    if isinstance(v, str):
        return v
    if isinstance(v, LabelValue):
        return repr(v)
    return format_number(v)


def format_number(v):
    if isinstance(v, FixedDec):
        return str(v)
    if isinstance(v, complex):
        re_s = format_number(v.real)
        im_s = format_number(abs(v.imag))
        return "%s%s%sI" % (re_s, "-" if v.imag < 0 else "+", im_s)
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return repr(v)
    return str(v)


def to_bits(v):
    if isinstance(v, BitStr):
        return v
    if isinstance(v, str):
        if any(c not in "01" for c in v):
            raise PLIError("cannot convert %r to bit" % v)
        return BitStr(v)
    n = to_number(v)
    return BitStr("1") if n != 0 else BitStr("0")


def is_true(v):
    if isinstance(v, BitStr):
        return "1" in v
    if isinstance(v, str):
        return "1" in v and set(v) <= {"0", "1"}
    return to_number(v) != 0


def convert(value, decl):
    """Convert a value to a declared type on assignment."""
    if decl is None:
        return value
    if decl.base == "FIXED":
        n = to_number(value)
        if isinstance(n, complex):
            n = n.real
        p, q = decl.prec if decl.prec else (None, 0)
        if q:
            # exact FIXED DECIMAL(p,q)
            try:
                if isinstance(n, FixedDec):
                    return n.to_precision(p or 15, q)
                mant = math.trunc(float(n) * 10 ** q)
                return FixedDec(mant, p or 15, q).to_precision(p or 15, q)
            except (SizeError, FixedOverflow) as e:
                raise PLICondition("SIZE", str(e))
        i = int(n)  # PL/I fixed assignment truncates toward zero
        if p is not None and len(str(abs(i))) > p:
            raise PLICondition("SIZE", "%r does not fit FIXED(%d)" % (i, p))
        return i
    if decl.base == "FLOAT":
        n = to_number(value)
        return n if isinstance(n, complex) else float(n)
    if decl.base == "COMPLEX":
        n = to_number(value)
        return complex(float(n), 0.0) if not isinstance(n, complex) else n
    if decl.base == "EVENT":
        if not isinstance(value, EventValue):
            raise PLIError("cannot assign %r to an EVENT" % (value,))
        return value
    if decl.base == "CHAR":
        s = to_string(value)
        if decl.length is not None and decl.length != "*":
            if decl.varying:
                return s[:decl.length]
            return s[:decl.length].ljust(decl.length)
        return s
    if decl.base == "BIT":
        b = to_bits(value)
        if decl.length is not None and decl.length != "*":
            return BitStr(b[:decl.length].ljust(decl.length, "0"))
        return b
    if decl.base == "LABEL":
        if value is not None and not isinstance(value, LabelValue):
            raise PLIError("cannot assign %r to a LABEL variable" % (value,))
        return value
    if decl.base == "POINTER":
        if not isinstance(value, Pointer):
            raise PLIError("cannot assign %r to a POINTER" % (value,))
        return Pointer(value.target)
    if decl.base == "PIC":
        if decl.pic.is_char:
            checked = decl.pic.validate_char(to_string(value))
            if checked is None:
                raise PLICondition("CONVERSION",
                                   "%r does not match picture %s"
                                   % (value, decl.pic.spec),
                                   source=to_string(value))
            return checked
        try:
            return decl.pic.assign(to_number(value))
        except PictureError as e:
            raise PLICondition("SIZE", str(e))
    return value


def unspec_bits(v):
    """Bit representation of a value (UNSPEC builtin)."""
    import struct
    if isinstance(v, BitStr):
        return v
    if isinstance(v, Pointer):
        return BitStr(format(id(v.target) & 0xFFFFFFFF, "032b"))
    if isinstance(v, str):
        return BitStr("".join(format(ord(c) & 0xFF, "08b") for c in v))
    if isinstance(v, float):
        raw = struct.unpack(">Q", struct.pack(">d", v))[0]
        return BitStr(format(raw, "064b"))
    if isinstance(v, int):
        return BitStr(format(v & 0xFFFFFFFF, "032b"))
    raise PLIError("UNSPEC: unsupported value %r" % (v,))


def unspec_decode(bits, decl):
    """Inverse of UNSPEC for the pseudo-variable UNSPEC(x) = bits."""
    import struct
    s = str(bits)
    if decl is None or decl.base == "BIT":
        return BitStr(s)
    if decl.base == "FIXED":
        if not s:
            return 0
        x = int(s, 2)
        if s[0] == "1" and len(s) >= 32:
            x -= 1 << len(s)
        return x
    if decl.base == "FLOAT":
        raw = int(s.ljust(64, "0")[:64], 2)
        return struct.unpack(">d", struct.pack(">Q", raw))[0]
    if decl.base in ("CHAR", "PIC"):
        chars = [chr(int(s[i:i + 8].ljust(8, "0"), 2))
                 for i in range(0, len(s), 8)]
        return "".join(chars)
    raise PLIError("UNSPEC pseudo-variable: unsupported target type")


LIST_TABS = 24  # PUT LIST tab stop interval (columns 1, 25, 49, ...)


# ---- the interpreter -----------------------------------------------------------

class Interpreter:
    def __init__(self, stdin=None, stdout=None):
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.column = 0          # current output column (0-based)
        self.input_tokens = []   # pending GET LIST tokens
        self.parser = PLIParser()
        self.cond_frames = []    # ON-unit frames, one per proc/begin activation
        self.current_cond = None # last raised condition (ONCODE etc.)
        self.string_input = False  # GET STRING in progress
        self.in_line = ""        # raw line buffer for GET EDIT
        self.in_pos = 0
        self.tasks = []          # attached task threads
        self.io_lock = threading.RLock()

    # -- program entry ----------------------------------------------------

    def run(self, source, include_dir="."):
        from .preproc import preprocess
        source = preprocess(source, include_dir)
        program = self.parser.parse(source)
        genv = Environment()
        main = None
        for stmt in program:
            if isinstance(stmt, N.Labeled) and isinstance(self._unlabel(stmt), N.ProcDef):
                name, proc = self._proc_of(stmt)
                genv.declare(name, Procedure(name, proc, genv))
                opts = [o.upper() for o in proc.options]
                if "MAIN" in opts or main is None:
                    main = genv.vars[name]
            elif not isinstance(stmt, N.Null):
                raise PLIError("only procedure definitions allowed at top level")
        if main is None:
            raise PLIError("no procedure found")
        try:
            self.call_procedure(main, [])
        except StopSignal:
            pass
        except PLICondition as c:
            self._flush_line()
            raise PLIError("unhandled condition %s: %s" % (c.name, c.msg))
        for t in self.tasks:      # let attached tasks finish
            t.join(timeout=10)
        self._flush_line()

    def _unlabel(self, stmt):
        while isinstance(stmt, N.Labeled):
            stmt = stmt.stmt
        return stmt

    def _proc_of(self, labeled):
        name = labeled.name
        return name, self._unlabel(labeled)

    # -- procedures ---------------------------------------------------------

    def call_procedure(self, proc, arg_cells):
        node = proc.node
        if len(arg_cells) != len(node.params):
            raise PLIError("%s: expected %d argument(s), got %d"
                           % (proc.name, len(node.params), len(arg_cells)))
        env = Environment(proc.env)
        for pname, cell in zip(node.params, arg_cells):
            env.declare(pname, cell)
        self._hoist_procs(node.body, env)
        self._hoist_labels(node.body, env)
        self.cond_frames.append({})
        try:
            self.exec_block(node.body, env)
        except ReturnSignal as r:
            value = r.value
            if value is not None and node.returns:
                value = convert(value, self._resolve_attrs(None, node.returns, env))
            return value
        finally:
            self.cond_frames.pop()
        return None

    def _hoist_labels(self, body, env):
        """Register label constants of this block activation (recursively,
        but not into nested procedures or BEGIN blocks)."""
        for stmt in body:
            self._hoist_labels_stmt(stmt, env)

    def _hoist_labels_stmt(self, stmt, env):
        if stmt is None:
            return
        if isinstance(stmt, N.Labeled):
            inner = self._unlabel(stmt)
            if isinstance(inner, N.FormatStmt):
                env.declare(stmt.name, FormatDef(inner.items))
            elif not isinstance(inner, N.ProcDef):
                env.declare(stmt.name, LabelConst(stmt.name))
                self._hoist_labels_stmt(stmt.stmt, env)
            return
        if isinstance(stmt, N.Prefix):
            self._hoist_labels_stmt(stmt.stmt, env)
        elif isinstance(stmt, N.If):
            self._hoist_labels_stmt(stmt.then, env)
            self._hoist_labels_stmt(stmt.els, env)
        elif isinstance(stmt, (N.Block, N.DoWhile, N.DoIter)):
            self._hoist_labels(stmt.body, env)
        elif isinstance(stmt, N.Select):
            for _, body in stmt.whens:
                self._hoist_labels_stmt(body, env)
            self._hoist_labels_stmt(stmt.otherwise, env)
        elif isinstance(stmt, N.On):
            self._hoist_labels_stmt(stmt.unit, env)

    def _hoist_procs(self, body, env):
        """Register nested procedure definitions before execution."""
        for stmt in body:
            if isinstance(stmt, N.Labeled):
                inner = self._unlabel(stmt)
                if isinstance(inner, N.ProcDef):
                    env.declare(stmt.name, Procedure(stmt.name, inner, env))

    # -- statement execution ---------------------------------------------------

    def exec_block(self, stmts, env):
        """Execute a statement list, handling GOTO to labels in this list."""
        labels = {}
        for i, s in enumerate(stmts):
            t = s
            while isinstance(t, (N.Labeled, N.Prefix)):
                if isinstance(t, N.Labeled):
                    labels[t.name] = i
                t = t.stmt
        i = 0
        while i < len(stmts):
            try:
                self.exec_stmt(stmts[i], env)
            except GotoSignal as g:
                if g.label in labels:
                    i = labels[g.label]
                    continue
                raise
            except PLICondition as c:
                try:
                    self.dispatch_condition(c)
                except GotoSignal as g:
                    if g.label in labels:
                        i = labels[g.label]
                        continue
                    raise
            i += 1

    def dispatch_condition(self, cond):
        """Run the established ON-unit for a raised condition.

        Normal return from the ON-unit resumes after the interrupted
        statement (the caller continues its loop); a GOTO inside the
        unit propagates as GotoSignal.  Unhandled conditions escalate
        to ERROR; unhandled ERROR terminates the program.
        """
        self.current_cond = cond
        for frame in reversed(self.cond_frames):
            handler = frame.get((cond.name, cond.qual),
                                frame.get((cond.name, None)))
            if handler is not None:
                if handler == "SYSTEM":
                    break
                unit, est_env = handler
                self.exec_stmt(unit, est_env)
                return
        # default (system) action
        if cond.name == "FINISH":
            return
        if cond.name != "ERROR":
            msg = "condition %s raised: %s" % (cond.name, cond.msg)
            err = PLICondition("ERROR", msg)
            err.code = cond.code
            return self.dispatch_condition(err)
        raise PLIError(cond.msg or "ERROR condition")

    def exec_stmt(self, stmt, env, loop_label=None):
        method = getattr(self, "exec_" + stmt.kind, None)
        if method is None:
            raise PLIError("line %d: cannot execute %s" % (stmt.lineno, stmt.kind))
        if stmt.kind in ("DoWhile", "DoIter"):
            return method(stmt, env, loop_label)
        return method(stmt, env)

    def exec_Labeled(self, stmt, env):
        inner = stmt
        while isinstance(inner, N.Labeled):
            label = inner.name
            inner = inner.stmt
        if isinstance(inner, N.ProcDef):
            return  # hoisted already
        self.exec_stmt(inner, env, loop_label=label)

    def exec_Null(self, stmt, env):
        pass

    def exec_FormatStmt(self, stmt, env):
        pass  # registered at block entry; no effect when flow reaches it

    def exec_ProcDef(self, stmt, env):
        raise PLIError("line %d: unlabeled PROCEDURE" % stmt.lineno)

    def exec_Declare(self, stmt, env):
        items = stmt.items
        i = 0
        while i < len(items):
            item = items[i]
            if item.level is None:
                for name in item.names:
                    self._declare_one(name, item, env)
                i += 1
            elif item.level == 1:
                j = i + 1
                while j < len(items) and (items[j].level or 0) > 1:
                    j += 1
                self._declare_structure(item, items[i + 1:j], env)
                i = j
            else:
                raise PLIError("line %d: level %d item %s without a level 1"
                               % (item.lineno, item.level, item.names[0]))

    def _declare_one(self, name, item, env):
        kinds = {k for k, _ in item.attrs}
        if "FILE" in kinds:
            f = PLIFile(name)
            for k, v in item.attrs:
                if k != "GENERIC":
                    continue
                gname, gargs = v
                if gname == "STREAM":
                    f.stream = True
                elif gname == "RECORD":
                    f.stream = False
                elif gname in ("INPUT", "OUTPUT", "UPDATE"):
                    f.mode = gname
                elif gname == "KEYED":
                    f.keyed = True
                elif gname == "PRINT":
                    f.stream = f.print_file = True
                elif gname == "TITLE" and gargs:
                    f.title = to_string(self.eval(gargs[0], env))
                elif gname in ("ENVIRONMENT", "ENV") and gargs:
                    for a in gargs:
                        n = getattr(a, "name", None)
                        if n == "INDEXED":
                            f.indexed = f.keyed = True
                elif gname in ("SEQUENTIAL", "DIRECT", "BUFFERED",
                               "UNBUFFERED", "EXTERNAL", "INTERNAL"):
                    pass
            env.declare(name, f)
            return
        if "BASED" in kinds:
            ptr = next(v for k, v in item.attrs if k == "BASED")
            env.declare(name, BasedVar(name, item, [], ptr))
            return
        if "CONTROLLED" in kinds:
            env.declare(name, Controlled(name, item))
            return
        if "DEFINED" in kinds:
            base_ref = next(v for k, v in item.attrs if k == "DEFINED")
            pos = next((v for k, v in item.attrs
                        if k == "GENERIC" and v[0] == "POSITION"), None)
            position = (int(to_number(self.eval(pos[1][0], env)))
                        if pos else 1)
            decl = self._resolve_attrs(name, item.attrs, env)
            env.declare(name, DefinedVar(name, decl, base_ref, position))
            return
        decl = self._resolve_attrs(name, item.attrs, env)
        init = next((val for kind, val in item.attrs if kind == "INIT"), None)
        existing = env.vars.get(name)
        if isinstance(existing, (PLIArray, PLIStructure)):
            # parameter (e.g. DCL V(*) FIXED): keep the caller's aggregate
            if isinstance(existing, PLIArray):
                existing.decl = decl
            return
        if isinstance(existing, Variable) and item.dims is None:
            # redeclaring a parameter: just retype the shared box
            existing.decl = decl
            if init is not None:
                existing.value = convert(self.eval(init[0], env), decl)
            return
        env.declare(name, self._make_entry(name, item, env))

    def _make_entry(self, name, item, env):
        decl = self._resolve_attrs(name, item.attrs, env)
        init = next((val for kind, val in item.attrs if kind == "INIT"), None)
        if item.dims is not None:
            bounds = []
            for b in item.dims:
                if b == ("*",):
                    raise PLIError("'*' bounds only valid for parameters")
                lo = 1 if b[0] is None else int(to_number(self.eval(b[0], env)))
                hi = int(to_number(self.eval(b[1], env)))
                bounds.append((lo, hi))
            arr = PLIArray(bounds, decl)
            if init is not None:
                vals = [convert(self.eval(e, env), decl) for e in init]
                for i in range(min(len(vals), len(arr.data))):
                    arr.data[i] = vals[i]
            return arr
        value = default_value(decl)
        if init is not None:
            value = convert(self.eval(init[0], env), decl)
        return Variable(value, decl)

    # -- structures -------------------------------------------------------

    def _declare_structure(self, root, subitems, env):
        name = root.names[0]
        if any(k == "BASED" for k, _ in root.attrs):
            ptr = next(v for k, v in root.attrs if k == "BASED")
            env.declare(name, BasedVar(name, root, subitems, ptr))
            return
        if any(k == "CONTROLLED" for k, _ in root.attrs):
            ctl = Controlled(name, root)
            ctl.subitems = subitems
            env.declare(name, ctl)
            return
        if isinstance(env.vars.get(name), PLIStructure):
            return  # structure parameter: keep the caller's structure
        if root.dims is not None:
            raise PLIError("line %d: arrays of structures are not supported"
                           % root.lineno)
        like = next((v for k, v in root.attrs if k == "LIKE"), None)
        if like is not None:
            tmpl = self._lookup_structure(like, env)
            base_level, subitems = tmpl.spec
            struct = PLIStructure(name)
            struct.spec = tmpl.spec
            self._build_members(struct, subitems, 0, base_level, env)
        else:
            struct = PLIStructure(name)
            struct.spec = (root.level, subitems)
            self._build_members(struct, subitems, 0, root.level, env)
        env.declare(name, struct)

    def _build_members(self, parent, items, i, parent_level, env):
        while i < len(items) and items[i].level > parent_level:
            it = items[i]
            nm = it.names[0]
            like = next((v for k, v in it.attrs if k == "LIKE"), None)
            is_node = (i + 1 < len(items) and items[i + 1].level > it.level)
            if like is not None:
                tmpl = self._lookup_structure(like, env)
                sub = PLIStructure(nm)
                sub.spec = tmpl.spec
                self._build_members(sub, tmpl.spec[1], 0, tmpl.spec[0], env)
                parent.members[nm] = sub
                i += 1
            elif is_node:
                sub = PLIStructure(nm)
                parent.members[nm] = sub
                i = self._build_members(sub, items, i + 1, it.level, env)
            else:
                parent.members[nm] = self._make_entry(nm, it, env)
                i += 1
        return i

    def _lookup_structure(self, ref, env):
        entry = env.lookup(ref.name)
        if entry is None:
            entry = self._search_member(ref.name, env)
        if not isinstance(entry, PLIStructure):
            raise PLIError("LIKE %s: not a structure" % ref.name)
        if entry.spec is None:
            raise PLIError("LIKE %s: structure has no declaration spec"
                           % ref.name)
        return entry

    def _search_member(self, name, env):
        """Unqualified reference to a structure member (unique in scope)."""
        hits = []
        e = env
        while e is not None:
            for entry in e.vars.values():
                if isinstance(entry, PLIStructure):
                    hit = entry.find(name)
                    if hit is not None:
                        hits.append(hit)
            if hits:          # innermost scope with a hit wins
                break
            e = e.parent
        if len(hits) > 1:
            raise PLIError("ambiguous unqualified reference %s" % name)
        return hits[0] if hits else None

    def _resolve_attrs(self, name, attrs, env):
        base = None
        length = None
        varying = False
        prec = None
        pic = None
        for kind, val in attrs:
            if kind == "FIXED":
                base = "FIXED"
                prec = val or prec
            elif kind == "FLOAT":
                base = "FLOAT"
                prec = val or prec
            elif kind in ("BINKW", "DECKW"):
                prec = val or prec
                if base is None:
                    base = "FIXED" if kind == "BINKW" else None
            elif kind == "CHARKW":
                base = "CHAR"
                length = ("*" if val == "*"
                          else int(to_number(self.eval(val, env))))
            elif kind == "BIT":
                base = "BIT"
                length = ("*" if val == "*"
                          else int(to_number(self.eval(val, env))))
            elif kind == "VARYING":
                varying = True
            elif kind == "LABEL":
                base = "LABEL"
            elif kind == "PICTURE":
                base = "PIC"
                pic = Picture(val)
            elif kind == "GENERIC":
                gname = val[0]
                if gname in ("POINTER", "PTR"):
                    base = "POINTER"
                elif gname == "COMPLEX":
                    base = "COMPLEX"
                elif gname == "EVENT":
                    base = "EVENT"
                elif gname in ("POSITION", "BUILTIN", "EXTERNAL", "INTERNAL",
                               "ALIGNED", "UNALIGNED", "REAL", "ABNORMAL",
                               "NORMAL", "TASK"):
                    pass
                else:
                    raise PLIError("unknown attribute %s" % gname)
            elif kind in ("INIT", "STATIC", "AUTOMATIC", "LIKE",
                          "BASED", "CONTROLLED", "DEFINED"):
                pass
        if base is None:
            base = (Decl.default_for(name).base if name
                    else "FLOAT")
        return Decl(base, length, varying, prec, pic)

    def exec_Assign(self, stmt, env):
        value = self.eval(stmt.value, env)
        self.assign_target(stmt.target, value, env)

    def assign_target(self, node, value, env):
        if isinstance(node, N.Member):
            self.assign_member(node, value, env)
        elif isinstance(node, N.PtrRef):
            self.assign_ptrref(node, value, env)
        else:
            self.assign_ref(node, value, env)

    def assign_ref(self, ref, value, env):
        name, args = ref.name, ref.args
        entry = env.lookup(name)
        if entry is None and name == "SUBSTR" and args:
            return self._assign_substr(args, value, env)
        if entry is None and name == "UNSPEC" and args:
            target = args[0]
            decl = None
            if isinstance(target, N.Ref):
                t_entry = env.lookup(target.name)
                if isinstance(t_entry, Variable):
                    decl = t_entry.decl
            self.assign_target(target, unspec_decode(to_bits(value), decl),
                               env)
            return
        if entry is None:
            entry = self._search_member(name, env)
        if entry is None:
            if args is not None:
                raise PLIError("line %d: %s is not declared as an array"
                               % (ref.lineno, name))
            decl = Decl.default_for(name)
            env.declare(name, Variable(convert(value, decl), decl))
            return
        self._assign_entry(entry, args, value, ref, env)

    def _assign_entry(self, entry, args, value, ref, env):
        if isinstance(entry, BasedVar):
            return self._assign_entry(self._based_target(entry, env),
                                      args, value, ref, env)
        if isinstance(entry, Controlled):
            if not entry.stack:
                raise PLIError("%s is not allocated" % entry.name)
            return self._assign_entry(entry.stack[-1], args, value, ref, env)
        if isinstance(entry, DefinedVar):
            return self._defined_write(entry, value, env)
        if isinstance(entry, PLIArray):
            if args is None:
                self._assign_array(entry, value)
                return
            subs = [self.eval(a, env) for a in args]
            entry.set(subs, value)
            return
        if isinstance(entry, Variable):
            if args is not None:
                raise PLIError("line %d: %s is not an array"
                               % (ref.lineno, ref.name))
            entry.value = convert(value, entry.decl)
            return
        if isinstance(entry, PLIStructure):
            self._assign_structure(entry, value)
            return
        raise PLIError("line %d: cannot assign to %s" % (ref.lineno, ref.name))

    def assign_member(self, node, value, env):
        base = self._eval_structure(node.base, env)
        entry = base.members.get(node.name)
        if entry is None:
            entry = base.find(node.name)
        if entry is None:
            raise PLIError("line %d: %s has no member %s"
                           % (node.lineno, base.name, node.name))
        self._assign_entry(entry, node.args, value, node, env)

    def _assign_array(self, arr, value):
        if isinstance(value, PLIArray):
            if len(value.data) != len(arr.data):
                raise PLIError("array assignment: extent mismatch")
            arr.data = [convert(v, arr.decl) for v in value.data]
        else:
            v = convert(value, arr.decl)
            arr.data = [v] * len(arr.data)

    def _assign_structure(self, struct, value):
        dst = list(struct.leaves())
        if isinstance(value, PLIStructure):
            src = list(value.leaves())
            if len(src) != len(dst):
                raise PLIError("structure assignment: %s and %s have "
                               "different shapes" % (struct.name, value.name))
            for d, s in zip(dst, src):
                if isinstance(d, PLIArray) and isinstance(s, PLIArray):
                    self._assign_array(d, s)
                elif isinstance(d, Variable) and isinstance(s, Variable):
                    d.value = convert(s.value, d.decl)
                else:
                    raise PLIError("structure assignment: member shape "
                                   "mismatch in %s" % struct.name)
        else:
            for d in dst:
                if isinstance(d, PLIArray):
                    self._assign_array(d, value)
                else:
                    d.value = convert(value, d.decl)

    def _eval_structure(self, node, env):
        v = self.eval(node, env)
        if not isinstance(v, PLIStructure):
            raise PLIError("qualified reference: %s is not a structure"
                           % getattr(node, "name", "?"))
        return v

    def _assign_substr(self, args, value, env):
        """SUBSTR(v, i [, len]) = expr;  pseudo-variable assignment."""
        target = args[0]
        if not isinstance(target, N.Ref):
            raise PLIError("SUBSTR pseudo-variable needs a variable reference")
        current = to_string(self.eval(target, env))
        i = int(to_number(self.eval(args[1], env)))
        length = (int(to_number(self.eval(args[2], env)))
                  if len(args) > 2 else len(current) - i + 1)
        s = to_string(value)
        s = s[:length].ljust(length)
        new = current[:i - 1] + s + current[i - 1 + length:]
        self.assign_target(target, new, env)

    def exec_CallStmt(self, stmt, env):
        entry = env.lookup(stmt.name)
        if not isinstance(entry, Procedure):
            raise PLIError("line %d: %s is not a procedure"
                           % (stmt.lineno, stmt.name))
        cells = [self._arg_cell(a, env) for a in stmt.args]
        opts = dict((k.upper(), v) for k, v in (stmt.opts or []))
        if "EVENT" in opts or "TASK" in opts:
            event = None
            if "EVENT" in opts:
                ev_val = self.eval(opts["EVENT"], env)
                if not isinstance(ev_val, EventValue):
                    raise PLIError("EVENT(...) needs an EVENT variable")
                event = ev_val
                event.ev.clear()
                event.status = 0
            self._spawn_task(entry, cells, event)
            return
        self.call_procedure(entry, cells)

    def _spawn_task(self, proc, cells, event):
        """Run a procedure as an attached task in its own interpreter
        (sharing storage boxes and the output stream)."""
        sub = Interpreter.__new__(Interpreter)
        sub.__dict__.update(self.__dict__)
        sub.cond_frames = []
        sub.current_cond = None
        sub.column = 0
        sub.tasks = []

        def body():
            try:
                sub.call_procedure(proc, cells)
            except (StopSignal, PLIError, PLICondition) as e:
                if event is not None:
                    event.status = 1
                sys.stderr.write("task %s: %s\n" % (proc.name, e))
            finally:
                if event is not None:
                    event.ev.set()

        t = threading.Thread(target=body, name=proc.name)
        t.start()
        self.tasks.append(t)

    def exec_WaitStmt(self, stmt, env):
        events = []
        for ref in stmt.refs:
            v = self.eval(ref, env)
            if not isinstance(v, EventValue):
                raise PLIError("WAIT: %s is not an EVENT"
                               % getattr(ref, "name", "?"))
            events.append(v)
        need = (int(to_number(self.eval(stmt.count, env)))
                if stmt.count is not None else len(events))
        remaining = list(events)
        done = 0
        while done < need and remaining:
            for e in list(remaining):
                if e.ev.wait(timeout=0.05):
                    remaining.remove(e)
                    done += 1
                    if done >= need:
                        break

    def _arg_cell(self, arg, env):
        """By-reference for plain variables, dummy Variable otherwise."""
        if isinstance(arg, N.Ref):
            entry = env.lookup(arg.name)
            if arg.args is None and isinstance(
                    entry, (Variable, PLIArray, PLIStructure)):
                return entry                     # by reference
        if isinstance(arg, N.Member) and arg.args is None:
            base = self._eval_structure(arg.base, env)
            entry = base.members.get(arg.name) or base.find(arg.name)
            if isinstance(entry, (Variable, PLIArray, PLIStructure)):
                return entry                     # by reference
        value = self.eval(arg, env)
        if isinstance(value, float):
            decl = Decl("FLOAT")
        elif isinstance(value, int):
            decl = Decl("FIXED")
        elif isinstance(value, BitStr):
            decl = Decl("BIT", len(value))
        else:
            decl = Decl("CHAR", len(value), varying=True)
        return Variable(value, decl)

    def exec_If(self, stmt, env):
        if is_true(self.eval(stmt.cond, env)):
            self.exec_stmt(stmt.then, env)
        elif stmt.els is not None:
            self.exec_stmt(stmt.els, env)

    def exec_Block(self, stmt, env):
        self._hoist_procs(stmt.body, env)
        self.exec_block(stmt.body, env)

    def exec_BeginBlock(self, stmt, env):
        inner = Environment(env)
        self._hoist_procs(stmt.body, inner)
        self._hoist_labels(stmt.body, inner)
        self.cond_frames.append({})
        try:
            self.exec_block(stmt.body, inner)
        finally:
            self.cond_frames.pop()

    def _loop_body(self, body, env, label):
        """Run a loop body once; returns 'leave', 'iterate' or None."""
        try:
            self.exec_block(body, env)
        except LeaveSignal as s:
            if s.label is None or s.label == label:
                return "leave"
            raise
        except IterateSignal as s:
            if s.label is None or s.label == label:
                return "iterate"
            raise
        return None

    def exec_DoWhile(self, stmt, env, label=None):
        while True:
            proceed = True
            for kw, expr in stmt.conds:
                if kw == "WHILE" and not is_true(self.eval(expr, env)):
                    proceed = False
            if not proceed:
                break
            if self._loop_body(stmt.body, env, label) == "leave":
                break
            done = False
            for kw, expr in stmt.conds:
                if kw == "UNTIL" and is_true(self.eval(expr, env)):
                    done = True
            if done:
                break

    def exec_DoIter(self, stmt, env, label=None):
        name = stmt.var
        entry = env.lookup(name)
        if entry is None:
            decl = Decl.default_for(name)
            entry = Variable(default_value(decl), decl)
            env.declare(name, entry)
        if not isinstance(entry, Variable):
            raise PLIError("DO control variable %s is not scalar" % name)
        for spec in stmt.specs:
            if self._run_spec(spec, entry, stmt.body, env, label) == "leave":
                return

    def _run_spec(self, spec, var, body, env, label):
        start = self.eval(spec.start, env)
        var.value = convert(start, var.decl)
        if spec.to is None and spec.by is None and not spec.conds:
            # single-value spec: execute once
            return self._loop_body(body, env, label)
        by = to_number(self.eval(spec.by, env)) if spec.by is not None else 1
        while True:
            if spec.to is not None:
                limit = to_number(self.eval(spec.to, env))
                cur = to_number(var.value)
                if (by >= 0 and cur > limit) or (by < 0 and cur < limit):
                    return None
            proceed = True
            for kw, expr in spec.conds:
                if kw == "WHILE" and not is_true(self.eval(expr, env)):
                    proceed = False
            if not proceed:
                return None
            res = self._loop_body(body, env, label)
            if res == "leave":
                return "leave"
            for kw, expr in spec.conds:
                if kw == "UNTIL" and is_true(self.eval(expr, env)):
                    return None
            var.value = convert(to_number(var.value) + by, var.decl)

    def exec_Select(self, stmt, env):
        subject = self.eval(stmt.subject, env) if stmt.subject is not None else None
        for exprs, body in stmt.whens:
            for e in exprs:
                v = self.eval(e, env)
                if subject is None:
                    hit = is_true(v)
                else:
                    hit = is_true(self._compare("EQ", subject, v))
                if hit:
                    self.exec_stmt(body, env)
                    return
        if stmt.otherwise is not None:
            self.exec_stmt(stmt.otherwise, env)
        else:
            raise PLIError("line %d: SELECT: no WHEN matched and no OTHERWISE"
                           % stmt.lineno)

    def exec_Goto(self, stmt, env):
        entry = env.lookup(stmt.label)
        if isinstance(entry, Variable) and entry.decl.base == "LABEL":
            if not isinstance(entry.value, LabelValue):
                raise PLIError("line %d: GOTO through uninitialized LABEL "
                               "variable %s" % (stmt.lineno, stmt.label))
            raise GotoSignal(entry.value.name)
        raise GotoSignal(stmt.label)

    def exec_On(self, stmt, env):
        key = (stmt.cond.upper(), stmt.qual)
        if stmt.unit is None:
            self.cond_frames[-1][key] = "SYSTEM"
        else:
            self.cond_frames[-1][key] = (stmt.unit, env)

    def exec_SignalStmt(self, stmt, env):
        raise PLICondition(stmt.cond, "SIGNAL %s" % stmt.cond, qual=stmt.qual)

    def exec_RevertStmt(self, stmt, env):
        self.cond_frames[-1].pop((stmt.cond.upper(), stmt.qual), None)

    def exec_Prefix(self, stmt, env):
        # condition prefixes like (SIZE): / (NOSUBSCRIPTRANGE): are parsed
        # and accepted; checking is always on in this interpreter
        self.exec_stmt(stmt.stmt, env)

    def exec_Return(self, stmt, env):
        raise ReturnSignal(self.eval(stmt.value, env)
                           if stmt.value is not None else None)

    def exec_Stop(self, stmt, env):
        raise StopSignal()

    def exec_Leave(self, stmt, env):
        raise LeaveSignal(stmt.label)

    def exec_Iterate(self, stmt, env):
        raise IterateSignal(stmt.label)

    # -- PUT / GET ---------------------------------------------------------

    def _write(self, text):
        with self.io_lock:
            self.stdout.write(text)
        nl = text.rfind("\n")
        if nl >= 0:
            self.column = len(text) - nl - 1
        else:
            self.column += len(text)

    def _newline(self, n=1):
        self._write("\n" * max(1, n))

    def _flush_line(self):
        if self.column > 0:
            self._write("\n")

    def exec_Put(self, stmt, env):
        fname = next((c[1] for c in stmt.clauses if c[0] == "FILE"), None)
        if fname is not None and fname != "SYSPRINT":
            f = self._file_entry(fname, env)
            self._open_file(f, f.mode or "OUTPUT")
            if not f.handle:
                raise PLIError("PUT FILE(%s): not a stream file" % fname)
            saved_out, saved_col = self.stdout, self.column
            self.stdout, self.column = f.handle, f.column
            try:
                self._put_clauses(
                    [c for c in stmt.clauses if c[0] != "FILE"], env)
            finally:
                f.column = self.column
                self.stdout, self.column = saved_out, saved_col
            return
        string_target = next((c[1] for c in stmt.clauses if c[0] == "STRING"),
                             None)
        if string_target is not None:
            import io
            saved_out, saved_col = self.stdout, self.column
            self.stdout, self.column = io.StringIO(), 0
            try:
                self._put_clauses(
                    [c for c in stmt.clauses if c[0] != "STRING"], env)
                text = self.stdout.getvalue()
            finally:
                self.stdout, self.column = saved_out, saved_col
            self.assign_target(string_target, text, env)
            return
        self._put_clauses(stmt.clauses, env)

    def _put_clauses(self, clauses, env):
        wrote_data = False
        for clause in clauses:
            kind = clause[0]
            if kind == "PAGE":
                self._flush_line()
            elif kind == "SKIP":
                n = (int(to_number(self.eval(clause[1], env)))
                     if clause[1] is not None else 1)
                self._newline(n)
            elif kind == "LIST":
                for e in clause[1]:
                    v = self.eval(e, env)
                    if isinstance(v, PLIStructure):
                        values = list(v.leaf_values())
                    elif isinstance(v, PLIArray):
                        values = list(v.data)
                    else:
                        values = [v]
                    for v in values:
                        if isinstance(v, BitStr):
                            text = "'%s'B" % v
                        elif isinstance(v, str):
                            text = v
                        elif isinstance(v, (LabelValue, Pointer)):
                            text = repr(v)
                        else:
                            text = format_number(v)
                        if wrote_data or self.column > 0:
                            pad = LIST_TABS - (self.column % LIST_TABS)
                            self._write(" " * pad)
                        self._write(text)
                        wrote_data = True
            elif kind == "EDIT":
                self._put_edit(clause[1], clause[2], env)
                wrote_data = True
            elif kind == "DATA":
                pairs = self._data_pairs(clause[1], env)
                for k, (nm, val) in enumerate(pairs):
                    if isinstance(val, (str,)) and not isinstance(val, (BitStr, PicStr)):
                        text = "%s='%s'" % (nm, val.replace("'", "''"))
                    elif isinstance(val, BitStr):
                        text = "%s='%s'B" % (nm, val)
                    else:
                        text = "%s=%s" % (nm, format_number(val)
                                          if not isinstance(val, PicStr)
                                          else str(val))
                    if k == len(pairs) - 1:
                        text += ";"
                    if wrote_data or self.column > 0:
                        pad = LIST_TABS - (self.column % LIST_TABS)
                        self._write(" " * pad)
                    self._write(text)
                    wrote_data = True

    def _data_pairs(self, refs, env):
        """(name, value) pairs for PUT DATA."""
        pairs = []
        if refs is None:
            e = env
            while e is not None:
                for name, entry in e.vars.items():
                    if isinstance(entry, (Variable, PLIArray, PLIStructure)):
                        self._collect_data(name, entry, pairs)
                e = e.parent
        else:
            for ref in refs:
                name = self._qref_text(ref, env)
                value = self.eval(ref, env)
                if isinstance(value, (PLIArray, PLIStructure)):
                    entry = value
                    self._collect_data(name, entry, pairs)
                else:
                    pairs.append((name, value))
        return pairs

    def _collect_data(self, name, entry, pairs):
        if isinstance(entry, Variable):
            if entry.decl.base != "LABEL":
                pairs.append((name, entry.value))
        elif isinstance(entry, PLIArray):
            for idx, val in zip(self._all_subscripts(entry), entry.data):
                pairs.append(("%s(%s)" % (name, ",".join(map(str, idx))), val))
        elif isinstance(entry, PLIStructure):
            for mname, m in entry.members.items():
                self._collect_data("%s.%s" % (name, mname), m, pairs)

    @staticmethod
    def _all_subscripts(arr):
        def rec(bounds):
            if not bounds:
                yield ()
                return
            (lo, hi), rest = bounds[0], bounds[1:]
            for i in range(lo, hi + 1):
                for tail in rec(rest):
                    yield (i,) + tail
        return rec(arr.bounds)

    def _qref_text(self, node, env):
        if isinstance(node, N.Ref):
            base = node.name
        elif isinstance(node, N.Member):
            base = "%s.%s" % (self._qref_text(node.base, env), node.name)
        else:
            raise PLIError("PUT DATA needs variable references")
        if getattr(node, "args", None):
            subs = [str(int(to_number(self.eval(a, env)))) for a in node.args]
            base += "(%s)" % ",".join(subs)
        return base

    def _expand_formats(self, formats, env, depth=0):
        """Expand R(label) remote format items."""
        if depth > 8:
            raise PLIError("R format items nested too deeply")
        out = []
        for f in formats:
            if f.name.upper() == "R" and f.args:
                ref = f.args[0]
                fd = env.lookup(ref.name) if isinstance(ref, N.Ref) else None
                if not isinstance(fd, FormatDef):
                    raise PLIError("R(%s) does not name a FORMAT statement"
                                   % getattr(ref, "name", "?"))
                out.extend(self._expand_formats(fd.items, env, depth + 1))
            else:
                out.append(f)
        return out

    def _put_edit(self, items, formats, env):
        formats = self._expand_formats(formats, env)
        fi = 0
        for item in items:
            # consume control formats (X, COL, SKIP) before each data item
            while True:
                f = formats[fi % len(formats)]
                name, args = f.name.upper(), [self.eval(a, env) for a in f.args]
                if name == "X":
                    self._write(" " * int(to_number(args[0])))
                elif name in ("COL", "COLUMN"):
                    col = int(to_number(args[0])) - 1
                    if col < self.column:
                        self._newline()
                    self._write(" " * (col - self.column))
                elif name == "SKIP":
                    self._newline(int(to_number(args[0])) if args else 1)
                else:
                    break
                fi += 1
            value = self.eval(item, env)
            self._write(self._format_value(value, name, args))
            fi += 1

    def _format_value(self, value, name, args):
        if name == "A":
            s = to_string(value)
            if args:
                w = int(to_number(args[0]))
                return s[:w].ljust(w)
            return s
        if name == "B":
            return str(to_bits(value))
        if name == "F":
            w = int(to_number(args[0]))
            d = int(to_number(args[1])) if len(args) > 1 else 0
            n = to_number(value)
            s = ("%.*f" % (d, n)) if d else str(int(round(n)))
            return s.rjust(w)[:w] if len(s) <= w else "*" * w
        if name == "E":
            w = int(to_number(args[0]))
            d = int(to_number(args[1])) if len(args) > 1 else 6
            return ("%.*E" % (d, to_number(value))).rjust(w)
        if name == "P":
            pic = Picture(to_string(args[0]))
            if pic.is_char:
                return to_string(value)[:pic.length].ljust(pic.length)
            return str(pic.edit(to_number(value)))
        raise PLIError("unsupported format item %s" % name)

    def exec_Get(self, stmt, env):
        fname = next((c[1] for c in stmt.clauses if c[0] == "FILE"), None)
        if fname is not None and fname != "SYSIN":
            f = self._file_entry(fname, env)
            self._open_file(f, f.mode or "INPUT")
            if not f.handle:
                raise PLIError("GET FILE(%s): not a stream file" % fname)
            saved = (self.stdin, self.input_tokens, self.in_line, self.in_pos)
            self.stdin = f.handle
            self.input_tokens, self.in_line, self.in_pos = \
                f.tokens, f.in_line, f.in_pos
            try:
                self._get_clauses(
                    [c for c in stmt.clauses if c[0] != "FILE"], env)
            except PLICondition as c:
                if c.name == "ENDFILE":
                    c.qual = fname
                raise
            finally:
                f.tokens, f.in_line, f.in_pos = \
                    self.input_tokens, self.in_line, self.in_pos
                (self.stdin, self.input_tokens,
                 self.in_line, self.in_pos) = saved
            return
        self._get_clauses(stmt.clauses, env)

    def _get_clauses(self, clauses, env):
        stmt = type("_G", (), {"clauses": clauses})  # lightweight shim
        string_src = next((c[1] for c in clauses if c[0] == "STRING"),
                          None)
        if string_src is not None:
            text = to_string(self.eval(string_src, env))
            saved, saved_flag = self.input_tokens, self.string_input
            self.input_tokens = [t for t in
                                 text.replace(",", " ").split() if t]
            self.string_input = True
            try:
                for clause in stmt.clauses:
                    if clause[0] == "LIST":
                        for ref in clause[1]:
                            self.assign_target(ref, self._next_input(), env)
            finally:
                self.input_tokens, self.string_input = saved, saved_flag
            return
        for clause in stmt.clauses:
            if clause[0] == "SKIP":
                self.input_tokens = []
                self.in_line, self.in_pos = "", 0
            elif clause[0] == "LIST":
                for ref in clause[1]:
                    self.assign_target(ref, self._next_input(), env)
            elif clause[0] == "DATA":
                self._get_data(env)
            elif clause[0] == "EDIT":
                self._get_edit(clause[1], clause[2], env)

    def _get_data(self, env):
        """GET DATA: read NAME=value pairs terminated by a semicolon."""
        import re
        text = ""
        while ";" not in text:
            line = self.stdin.readline()
            if not line:
                raise PLICondition("ENDFILE", "end of file in GET DATA",
                                   qual="SYSIN")
            text += line.lstrip(chr(0xFEFF))
        text = text[:text.index(";")]
        pat = (r"([A-Za-z_$#@][A-Za-z0-9_$#@.]*)\s*"
               r"(?:\(([^)]*)\))?\s*=\s*('(?:[^']|'')*'|[^,\s]+)")
        for m in re.finditer(pat, text):
            name, subs, raw = m.groups()
            if raw.startswith("'"):
                value = raw[1:-1].replace("''", "'")
            else:
                try:
                    value = int(raw)
                except ValueError:
                    try:
                        value = float(raw)
                    except ValueError:
                        value = raw
            parts = name.upper().split(".")
            entry = env.lookup(parts[0])
            if entry is None:
                entry = self._search_member(parts[0], env)
            for part in parts[1:]:
                if not isinstance(entry, PLIStructure):
                    entry = None
                    break
                entry = entry.members.get(part) or entry.find(part)
            if entry is None:
                raise PLIError("GET DATA: unknown name %s" % name)
            if isinstance(entry, PLIArray):
                if not subs:
                    raise PLIError("GET DATA: %s needs subscripts" % name)
                entry.set([int(s) for s in subs.split(",")], value)
            elif isinstance(entry, Variable):
                entry.value = convert(value, entry.decl)
            else:
                raise PLIError("GET DATA: cannot assign %s" % name)

    def _read_chars(self, n):
        """Read exactly n characters from the input stream (GET EDIT)."""
        out = []
        got = 0
        while got < n:
            if self.in_pos >= len(self.in_line):
                line = self.stdin.readline()
                if not line:
                    raise PLICondition("ENDFILE",
                                       "end of input file on GET EDIT",
                                       qual="SYSIN")
                self.in_line = line.lstrip(chr(0xFEFF)).rstrip("\r\n")
                self.in_pos = 0
                continue
            take = min(n - got, len(self.in_line) - self.in_pos)
            out.append(self.in_line[self.in_pos:self.in_pos + take])
            self.in_pos += take
            got += take
        return "".join(out)

    def _get_edit(self, refs, formats, env):
        formats = self._expand_formats(formats, env)
        fi = 0
        for ref in refs:
            while True:
                f = formats[fi % len(formats)]
                name = f.name.upper()
                args = [self.eval(a, env) for a in f.args]
                if name == "X":
                    self._read_chars(int(to_number(args[0])))
                elif name in ("COL", "COLUMN"):
                    col = int(to_number(args[0])) - 1
                    if self.in_pos > col:
                        self.in_line, self.in_pos = "", 0
                    if self.in_pos < col:
                        self._read_chars(col - self.in_pos)
                elif name == "SKIP":
                    self.in_line, self.in_pos = "", 0
                else:
                    break
                fi += 1
            if name == "A":
                w = int(to_number(args[0])) if args else 0
                value = self._read_chars(w) if w else ""
            elif name in ("F", "E"):
                w = int(to_number(args[0]))
                d = int(to_number(args[1])) if len(args) > 1 else 0
                raw = self._read_chars(w).strip()
                if not raw:
                    value = 0
                else:
                    value = to_number(raw)
                    if d and isinstance(value, int) and "." not in raw:
                        value = value / 10 ** d
            elif name == "P":
                pic = Picture(to_string(args[0]))
                raw = self._read_chars(pic.length)
                value = raw if pic.is_char else pic.value(raw)
            else:
                raise PLIError("unsupported GET EDIT format item %s" % name)
            self.assign_target(ref, value, env)
            fi += 1

    def _next_input(self):
        if self.string_input and not self.input_tokens:
            raise PLICondition("ERROR", "GET STRING: source string exhausted")
        while not self.input_tokens:
            line = self.stdin.readline()
            if not line:
                raise PLICondition("ENDFILE", "end of input file on GET",
                                   qual="SYSIN")
            line = line.lstrip(chr(0xFEFF))  # BOM from Windows pipes
            self.input_tokens = [t for t in
                                 line.replace(",", " ").split() if t]
        tok = self.input_tokens.pop(0)
        if tok.startswith("'") and tok.endswith("'") and len(tok) >= 2:
            return tok[1:-1]
        try:
            return int(tok)
        except ValueError:
            try:
                return float(tok)
            except ValueError:
                return tok

    # -- expressions -----------------------------------------------------------

    def eval(self, node, env):
        method = getattr(self, "eval_" + node.kind, None)
        if method is None:
            raise PLIError("cannot evaluate %s" % node.kind)
        return method(node, env)

    def eval_Num(self, node, env):
        return node.value

    def eval_Str(self, node, env):
        return node.value

    def eval_Bits(self, node, env):
        return BitStr(node.value)

    def eval_Ref(self, node, env):
        entry = env.lookup(node.name)
        if isinstance(entry, Procedure):
            cells = [self._arg_cell(a, env) for a in (node.args or [])]
            result = self.call_procedure(entry, cells)
            if result is None:
                raise PLIError("procedure %s returned no value" % node.name)
            return result
        if isinstance(entry, LabelConst):
            return LabelValue(entry.name)
        if entry is None:
            entry = self._search_member(node.name, env)
        if entry is None:
            if node.args is not None or node.name in _NILADIC_BUILTINS:
                return self.call_builtin(node, env)
            # implicit declaration on first (read) use
            decl = Decl.default_for(node.name)
            var = Variable(default_value(decl), decl)
            env.declare(node.name, var)
            return var.value
        return self._eval_entry(entry, node, env)

    def _eval_entry(self, entry, node, env):
        if isinstance(entry, BasedVar):
            return self._eval_entry(self._based_target(entry, env), node, env)
        if isinstance(entry, Controlled):
            if not entry.stack:
                raise PLIError("%s is not allocated" % entry.name)
            return self._eval_entry(entry.stack[-1], node, env)
        if isinstance(entry, DefinedVar):
            return self._defined_read(entry, env)
        if isinstance(entry, Variable):
            if node.args is not None:
                raise PLIError("line %d: %s is not an array/function"
                               % (node.lineno, node.name))
            return entry.value
        if isinstance(entry, PLIArray):
            if node.args is None:
                return entry     # whole-array reference (aggregate)
            subs = [self.eval(a, env) for a in node.args]
            return entry.get(subs)
        if isinstance(entry, PLIStructure):
            if node.args is not None:
                raise PLIError("line %d: %s is a structure, not an array"
                               % (node.lineno, node.name))
            return entry
        raise PLIError("line %d: cannot evaluate %s" % (node.lineno, node.name))

    # -- based / controlled / defined storage --------------------------------

    def _based_target(self, bv, env):
        if bv.ptr_ref is None:
            raise PLIError("BASED variable %s has no pointer; use P->%s"
                           % (bv.name, bv.name))
        ptr = self.eval(bv.ptr_ref, env)
        if not isinstance(ptr, Pointer):
            raise PLIError("%s: BASED qualifier is not a pointer" % bv.name)
        if ptr.target is None:
            raise PLIError("%s: NULL pointer dereference" % bv.name)
        return ptr.target

    def _defined_base(self, dv, env):
        entry = env.lookup(dv.base_ref.name)
        if entry is None:
            entry = self._search_member(dv.base_ref.name, env)
        if entry is None:
            raise PLIError("DEFINED %s: base %s not found"
                           % (dv.name, dv.base_ref.name))
        return entry

    def _defined_read(self, dv, env):
        base = self._defined_base(dv, env)
        if isinstance(base, Variable):
            if dv.decl.base in ("CHAR", "BIT") and \
                    isinstance(base.value, str):
                s = to_string(base.value)
                ln = dv.decl.length if dv.decl.length not in (None, "*") \
                    else len(s) - dv.position + 1
                return convert(s[dv.position - 1:dv.position - 1 + ln],
                               dv.decl)
            return convert(base.value, dv.decl)
        if isinstance(base, (PLIArray, PLIStructure)):
            return base
        raise PLIError("DEFINED %s: unsupported base" % dv.name)

    def _defined_write(self, dv, value, env):
        base = self._defined_base(dv, env)
        if isinstance(base, Variable):
            if dv.decl.base in ("CHAR", "BIT") and \
                    isinstance(base.value, str):
                s = to_string(base.value)
                ln = dv.decl.length if dv.decl.length not in (None, "*") \
                    else len(s) - dv.position + 1
                seg = to_string(convert(value, dv.decl))[:ln].ljust(ln)
                new = s[:dv.position - 1] + seg + s[dv.position - 1 + ln:]
                base.value = convert(new, base.decl)
                return
            base.value = convert(value, base.decl)
            return
        raise PLIError("DEFINED %s: cannot assign through this overlay"
                       % dv.name)

    def eval_PtrRef(self, node, env):
        entry = self._ptr_entry(node, env)
        return self._eval_entry(entry, node, env)

    def assign_ptrref(self, node, value, env):
        entry = self._ptr_entry(node, env)
        self._assign_entry(entry, node.args, value, node, env)

    def _ptr_entry(self, node, env):
        ptr = self.eval(node.base, env)
        if not isinstance(ptr, Pointer):
            raise PLIError("line %d: -> applied to a non-pointer"
                           % node.lineno)
        if ptr.target is None:
            raise PLIError("line %d: NULL pointer dereference (->%s)"
                           % (node.lineno, node.name))
        entry = ptr.target
        if isinstance(entry, PLIStructure) and entry.name != node.name:
            m = entry.members.get(node.name) or entry.find(node.name)
            if m is not None:
                return m
        return entry

    def exec_AllocStmt(self, stmt, env):
        for name, set_ref in stmt.items:
            entry = env.lookup(name)
            if isinstance(entry, BasedVar):
                alloc = self._instantiate(entry.name, entry.item,
                                          entry.subitems, env)
                ptr_ref = set_ref or entry.ptr_ref
                if ptr_ref is None:
                    raise PLIError("ALLOCATE %s: no SET pointer" % name)
                self.assign_target(ptr_ref, Pointer(alloc), env)
            elif isinstance(entry, Controlled):
                entry.stack.append(self._instantiate(
                    entry.name, entry.item,
                    getattr(entry, "subitems", []), env))
            else:
                raise PLIError("ALLOCATE %s: not BASED or CONTROLLED" % name)

    def _instantiate(self, name, item, subitems, env):
        if subitems:
            struct = PLIStructure(name)
            struct.spec = (item.level, subitems)
            self._build_members(struct, subitems, 0, item.level, env)
            return struct
        return self._make_entry(name, item, env)

    def exec_FreeStmt(self, stmt, env):
        for name in stmt.names:
            entry = env.lookup(name)
            if isinstance(entry, BasedVar):
                if entry.ptr_ref is not None:
                    self.assign_target(entry.ptr_ref, Pointer(None), env)
            elif isinstance(entry, Controlled):
                if not entry.stack:
                    raise PLIError("FREE %s: not allocated" % name)
                entry.stack.pop()
            else:
                raise PLIError("FREE %s: not BASED or CONTROLLED" % name)

    # -- record I/O -------------------------------------------------------

    def _file_entry(self, name, env, create=True):
        entry = env.lookup(name)
        if isinstance(entry, PLIFile):
            return entry
        if entry is None and create:
            f = PLIFile(name)
            if name == "SYSPRINT":
                f.stream = f.print_file = True
            if name == "SYSIN":
                f.stream = True
                f.mode = "INPUT"
            env.declare(name, f)
            return f
        raise PLIError("%s is not a file" % name)

    def _file_path(self, f):
        return f.title or (f.name.lower() + ".dat")

    def _open_file(self, f, mode=None):
        if f.is_open:
            return
        f.mode = mode or f.mode or "INPUT"
        path = self._file_path(f)
        try:
            if f.indexed:
                f.index = {}
                if f.mode in ("INPUT", "UPDATE") and os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as h:
                        for line in h:
                            if "\t" in line:
                                k, _, v = line.rstrip("\n").partition("\t")
                                f.index[k] = v
                f.seq_keys = None
                f.dirty = False
            else:
                pymode = {"INPUT": "r", "OUTPUT": "w", "UPDATE": "r+"}[f.mode]
                f.handle = open(path, pymode, encoding="utf-8")
        except OSError as e:
            raise PLICondition("UNDEFINEDFILE",
                               "cannot open file %s (%s): %s"
                               % (f.name, path, e), qual=f.name)

    def _close_file(self, f):
        if f.indexed and f.index is not None:
            if f.dirty:
                with open(self._file_path(f), "w", encoding="utf-8") as h:
                    for k in sorted(f.index):
                        h.write("%s\t%s\n" % (k, f.index[k]))
            f.index = None
            f.seq_keys = None
        if f.handle is not None:
            f.handle.close()
            f.handle = None
        f.tokens, f.in_line, f.in_pos, f.column = [], "", 0, 0

    def exec_IOStmt(self, stmt, env):
        opts = dict((k.upper(), v) for k, v in stmt.opts)
        fname = opts.get("FILE")
        if fname is None:
            raise PLIError("line %d: %s needs FILE(...)"
                           % (stmt.lineno, stmt.verb))
        f = self._file_entry(fname, env)
        verb = stmt.verb
        if verb == "OPEN":
            for m in ("INPUT", "OUTPUT", "UPDATE"):
                if m in opts:
                    f.mode = m
            if "TITLE" in opts and opts["TITLE"] is not None:
                f.title = to_string(self.eval(opts["TITLE"], env))
            if "STREAM" in opts:
                f.stream = True
            if "RECORD" in opts:
                f.stream = False
            if "PRINT" in opts:
                f.stream = f.print_file = True
            self._open_file(f)
            return
        if verb == "CLOSE":
            self._close_file(f)
            return
        if verb == "READ":
            self._open_file(f, f.mode or "INPUT")
            into = opts.get("INTO")
            if into is None:
                raise PLIError("READ needs INTO(...)")
            if f.indexed:
                if "KEY" in opts:
                    key = to_string(self.eval(opts["KEY"], env)).strip()
                    if key not in f.index:
                        raise PLICondition("KEY", "key %r not found in %s"
                                           % (key, f.name), qual=f.name)
                    record = f.index[key]
                else:
                    if f.seq_keys is None:
                        f.seq_keys = iter(sorted(f.index))
                    try:
                        key = next(f.seq_keys)
                    except StopIteration:
                        raise PLICondition("ENDFILE", "end of file %s"
                                           % f.name, qual=f.name)
                    record = f.index[key]
                if "KEYTO" in opts:
                    self.assign_target(opts["KEYTO"], key, env)
            else:
                line = f.handle.readline()
                if not line:
                    raise PLICondition("ENDFILE", "end of file %s" % f.name,
                                       qual=f.name)
                record = line.rstrip("\n")
            self._record_into(into, record, env)
            return
        if verb in ("WRITE", "REWRITE"):
            self._open_file(f, f.mode or ("UPDATE" if verb == "REWRITE"
                                          else "OUTPUT"))
            from_ = opts.get("FROM")
            if from_ is None:
                raise PLIError("%s needs FROM(...)" % verb)
            record = self._record_from(from_, env)
            if f.indexed:
                if verb == "WRITE":
                    keyopt = opts.get("KEYFROM")
                else:
                    keyopt = opts.get("KEY", opts.get("KEYFROM"))
                if keyopt is None:
                    raise PLIError("%s on INDEXED file needs a key" % verb)
                key = to_string(self.eval(keyopt, env)).strip()
                if verb == "REWRITE" and key not in f.index:
                    raise PLICondition("KEY", "key %r not found" % key,
                                       qual=f.name)
                f.index[key] = record
                f.dirty = True
            else:
                if verb == "REWRITE":
                    raise PLIError("REWRITE requires an INDEXED file")
                f.handle.write(record + "\n")
            return
        if verb == "DELETE":
            self._open_file(f, f.mode or "UPDATE")
            if not f.indexed or "KEY" not in opts:
                raise PLIError("DELETE needs an INDEXED file and KEY(...)")
            key = to_string(self.eval(opts["KEY"], env)).strip()
            if key not in f.index:
                raise PLICondition("KEY", "key %r not found" % key,
                                   qual=f.name)
            del f.index[key]
            f.dirty = True
            return

    def _leaf_width(self, decl):
        if decl.base == "CHAR":
            return decl.length if isinstance(decl.length, int) else 24
        if decl.base == "PIC":
            return decl.pic.length
        if decl.base == "BIT":
            return decl.length if isinstance(decl.length, int) else 8
        if decl.base == "FIXED":
            return 12
        return 24                    # FLOAT and everything else

    def _record_from(self, ref, env):
        value = self.eval(ref, env)
        if isinstance(value, PLIStructure):
            parts = []
            for leaf in value.leaves():
                if isinstance(leaf, PLIArray):
                    for v in leaf.data:
                        w = self._leaf_width(leaf.decl)
                        parts.append(to_string(v)[:w].rjust(w)
                                     if leaf.decl.base in ("FIXED", "FLOAT")
                                     else to_string(v)[:w].ljust(w))
                else:
                    w = self._leaf_width(leaf.decl)
                    s = to_string(leaf.value)
                    parts.append(s[:w].rjust(w)
                                 if leaf.decl.base in ("FIXED", "FLOAT")
                                 else s[:w].ljust(w))
            return "".join(parts)
        return to_string(value)

    def _record_into(self, ref, record, env):
        entry = None
        if isinstance(ref, N.Ref) and ref.args is None:
            entry = env.lookup(ref.name) or self._search_member(ref.name, env)
        if isinstance(entry, BasedVar):
            entry = self._based_target(entry, env)
        if isinstance(entry, PLIStructure):
            pos = 0
            for leaf in entry.leaves():
                if isinstance(leaf, PLIArray):
                    w = self._leaf_width(leaf.decl)
                    for i in range(len(leaf.data)):
                        leaf.data[i] = convert(record[pos:pos + w].strip()
                                               if leaf.decl.base in
                                               ("FIXED", "FLOAT")
                                               else record[pos:pos + w],
                                               leaf.decl)
                        pos += w
                else:
                    w = self._leaf_width(leaf.decl)
                    chunk = record[pos:pos + w]
                    if leaf.decl.base in ("FIXED", "FLOAT"):
                        chunk = chunk.strip() or "0"
                    leaf.value = convert(chunk, leaf.decl)
                    pos += w
            return
        self.assign_target(ref, record, env)

    def eval_Member(self, node, env):
        base = self._eval_structure(node.base, env)
        entry = base.members.get(node.name)
        if entry is None:
            entry = base.find(node.name)
        if entry is None:
            raise PLIError("line %d: %s has no member %s"
                           % (node.lineno, base.name, node.name))
        return self._eval_entry(entry, node, env)

    def eval_UnOp(self, node, env):
        v = self.eval(node.operand, env)
        if node.op == "MINUS":
            return -to_number(v)
        if node.op == "PLUS":
            return +to_number(v)
        if node.op == "NOT":
            b = to_bits(v) if isinstance(v, (str, BitStr)) else to_bits(v)
            return BitStr("".join("1" if c == "0" else "0" for c in b)) \
                if b else BitStr("1" if not is_true(v) else "0")
        raise PLIError("unknown unary op %s" % node.op)

    def eval_BinOp(self, node, env):
        op = node.op
        left = self.eval(node.left, env)
        if op in ("AND", "OR"):
            return self._bitop(op, left, self.eval(node.right, env))
        right = self.eval(node.right, env)
        if op == "CONCAT":
            if isinstance(left, BitStr) and isinstance(right, BitStr):
                return BitStr(str(left) + str(right))
            return to_string(left) + to_string(right)
        if op in ("EQ", "NE", "LT", "LE", "GT", "GE"):
            return self._compare(op, left, right)
        a, b = to_number(left), to_number(right)
        # FIXED DECIMAL mixes with float/complex as floating-point
        if isinstance(a, FixedDec) and isinstance(b, (float, complex)):
            a = float(a)
        if isinstance(b, FixedDec) and isinstance(a, (float, complex)):
            b = float(b)
        try:
            if op == "PLUS":
                return a + b
            if op == "MINUS":
                return a - b
            if op == "STAR":
                return a * b
            if op == "SLASH":
                if b == 0:
                    raise PLICondition("ZERODIVIDE",
                                       "line %d: division by zero"
                                       % node.lineno)
                r = a / b
                return int(r) if isinstance(a, int) and isinstance(b, int) \
                    and a % b == 0 else r
            if op == "POW":
                r = a ** b
                if isinstance(a, int) and isinstance(b, int) and b >= 0:
                    return int(r)
                return r
        except FixedOverflow as e:
            raise PLICondition("FIXEDOVERFLOW",
                               "line %d: %s" % (node.lineno, e))
        except ZeroDivisionError:
            raise PLICondition("ZERODIVIDE",
                               "line %d: division by zero" % node.lineno)
        raise PLIError("unknown operator %s" % op)

    def _compare(self, op, left, right):
        if isinstance(left, Pointer) or isinstance(right, Pointer):
            if op not in ("EQ", "NE"):
                raise PLIError("pointers only compare with = and ^=")
            eq = left == right
            return BitStr("1" if (eq if op == "EQ" else not eq) else "0")
        if isinstance(left, str) and isinstance(right, str) \
                and not isinstance(left, BitStr) and not isinstance(right, BitStr):
            # character comparison: pad shorter with blanks (PL/I rule)
            n = max(len(left), len(right))
            a, b = left.ljust(n), right.ljust(n)
        else:
            try:
                a, b = to_number(left), to_number(right)
            except PLIError:
                a, b = to_string(left), to_string(right)
            if isinstance(a, complex) or isinstance(b, complex):
                if op not in ("EQ", "NE"):
                    raise PLIError("COMPLEX values only compare with = / ^=")
                eq = a == b
                return BitStr("1" if (eq if op == "EQ" else not eq) else "0")
        result = {
            "EQ": a == b, "NE": a != b, "LT": a < b,
            "LE": a <= b, "GT": a > b, "GE": a >= b,
        }[op]
        return BitStr("1" if result else "0")

    def _bitop(self, op, left, right):
        lb, rb = to_bits(left), to_bits(right)
        n = max(len(lb), len(rb), 1)
        lb, rb = lb.ljust(n, "0"), rb.ljust(n, "0")
        if op == "AND":
            return BitStr("".join("1" if x == "1" and y == "1" else "0"
                                  for x, y in zip(lb, rb)))
        return BitStr("".join("1" if x == "1" or y == "1" else "0"
                              for x, y in zip(lb, rb)))

    # -- builtin functions --------------------------------------------------------

    def call_builtin(self, node, env):
        name = node.name
        if name not in _BUILTINS and name not in _NILADIC_BUILTINS:
            raise PLIError("line %d: %s is not declared and is not a builtin"
                           % (node.lineno, name))
        if name in _UNEVALUATED_BUILTINS:
            args = []  # the argument is a reference, not an expression
        else:
            args = [self.eval(a, env) for a in (node.args or [])]
        return builtin_dispatch(self, name, args, node, env)


_NILADIC_BUILTINS = {"DATE", "TIME", "DATETIME", "ONCODE", "ONCHAR",
                     "ONSOURCE", "NULL", "RANDOM"}
_UNEVALUATED_BUILTINS = {"HBOUND", "LBOUND", "DIM", "ADDR", "ALLOCATION"}

_BUILTINS = {
    "ABS", "MOD", "REM", "MIN", "MAX", "SIGN", "CEIL", "FLOOR", "TRUNC",
    "ROUND", "SQRT", "EXP", "LOG", "LOG2", "LOG10", "SIN", "COS", "TAN",
    "ASIN", "ACOS", "ATAN", "SIND", "COSD", "TAND",
    "SUBSTR", "LENGTH", "INDEX", "VERIFY", "TRANSLATE", "REPEAT", "COPY",
    "TRIM", "LOWERCASE", "UPPERCASE", "CHAR", "BIT", "FIXED", "FLOAT",
    "BINARY", "DECIMAL", "HBOUND", "LBOUND", "DIM", "DATE", "TIME",
    "ONCODE", "ONCHAR", "ONSOURCE",
    "NULL", "ADDR", "ALLOCATION", "UNSPEC",
    "REAL", "IMAG", "CONJG", "COMPLEX", "COMPLETION", "STATUS",
    "LEFT", "RIGHT", "CENTER", "CENTRE", "REVERSE", "SEARCH", "SEARCHR",
    "VERIFYR", "TALLY", "HIGH", "LOW", "BOOL", "STRING",
    "SINH", "COSH", "TANH", "ATANH", "ERF", "ERFC", "ATAND",
    "ADD", "SUBTRACT", "MULTIPLY", "DIVIDE",
    "SUM", "PROD", "DATETIME", "RANDOM",
}


def builtin_dispatch(interp, name, args, node, env):
    def num(i):
        return to_number(args[i])

    def s(i):
        return to_string(args[i])

    if name == "ABS":
        return abs(num(0))
    if name in ("MOD", "REM"):
        a, b = num(0), num(1)
        if b == 0:
            raise PLICondition("ZERODIVIDE", "zero divisor in %s" % name)
        r = math.fmod(a, b) if name == "REM" else a - b * math.floor(a / b)
        return int(r) if isinstance(a, int) and isinstance(b, int) else r
    if name == "MIN":
        return min(to_number(a) for a in args)
    if name == "MAX":
        return max(to_number(a) for a in args)
    if name == "SIGN":
        n = num(0)
        return (n > 0) - (n < 0)
    if name == "CEIL":
        return int(math.ceil(num(0)))
    if name == "FLOOR":
        return int(math.floor(num(0)))
    if name == "TRUNC":
        return int(math.trunc(num(0)))
    if name == "ROUND":
        d = int(num(1)) if len(args) > 1 else 0
        x = num(0)
        if isinstance(x, FixedDec):        # exact half-away-from-zero
            if x.q <= d:
                return x
            div = 10 ** (x.q - d)
            m = (abs(x.mant) + div // 2) // div
            return FixedDec(-m if x.mant < 0 else m, x.p, max(d, 0))
        r = round(x, d)
        return int(r) if d <= 0 else r
    if name == "SQRT":
        return math.sqrt(num(0))
    if name == "EXP":
        return math.exp(num(0))
    if name == "LOG":
        return math.log(num(0))
    if name == "LOG2":
        return math.log2(num(0))
    if name == "LOG10":
        return math.log10(num(0))
    if name == "ATAN" and len(args) > 1:
        return math.atan2(num(0), num(1))
    if name == "ATAND":
        return math.degrees(math.atan2(num(0), num(1)) if len(args) > 1
                            else math.atan(num(0)))
    if name in ("SIN", "COS", "TAN", "ASIN", "ACOS", "ATAN",
                "SINH", "COSH", "TANH", "ATANH", "ERF", "ERFC"):
        return getattr(math, name.lower())(num(0))
    if name in ("SIND", "COSD", "TAND"):
        return getattr(math, name[:3].lower())(math.radians(num(0)))
    if name == "SUBSTR":
        text = s(0)
        i = int(num(1))
        length = int(num(2)) if len(args) > 2 else len(text) - i + 1
        if i < 1 or length < 0 or i - 1 + length > len(text):
            raise PLICondition("STRINGRANGE",
                               "line %d: SUBSTR(%r,%d,%d) out of range"
                               % (node.lineno, text, i, length))
        result = text[i - 1:i - 1 + length]
        return BitStr(result) if isinstance(args[0], BitStr) else result
    if name == "LENGTH":
        return len(s(0)) if not isinstance(args[0], BitStr) else len(args[0])
    if name == "INDEX":
        start = int(num(2)) if len(args) > 2 else 1
        return s(0).find(s(1), max(start - 1, 0)) + 1
    if name == "VERIFY":
        text, allowed = s(0), s(1)
        start = int(num(2)) if len(args) > 2 else 1
        for i in range(max(start - 1, 0), len(text)):
            if text[i] not in allowed:
                return i + 1
        return 0
    if name == "TRANSLATE":
        text, to, frm = s(0), s(1), (s(2) if len(args) > 2 else None)
        if frm is None:
            return text  # degenerate form
        table = {}
        for i, c in enumerate(frm):
            table[c] = to[i] if i < len(to) else " "
        return "".join(table.get(c, c) for c in text)
    if name in ("REPEAT", "COPY"):
        n = int(num(1))
        base = s(0)
        # REPEAT(x,n) gives n+1 copies; COPY(x,n) gives n copies
        count = n + 1 if name == "REPEAT" else n
        result = base * max(0, count)
        return BitStr(result) if isinstance(args[0], BitStr) else result
    if name == "TRIM":
        if len(args) > 1:
            text = s(0).lstrip(s(1))
            return text.rstrip(s(2) if len(args) > 2 else s(1))
        return s(0).strip()
    if name == "LOWERCASE":
        return s(0).lower()
    if name == "UPPERCASE":
        return s(0).upper()
    if name == "CHAR":
        text = to_string(args[0])
        if len(args) > 1:
            w = int(num(1))
            return text[:w].ljust(w)
        return text
    if name == "BIT":
        b = to_bits(args[0]) if isinstance(args[0], (str, BitStr)) \
            else BitStr(bin(int(num(0)))[2:])
        if len(args) > 1:
            w = int(num(1))
            return BitStr(str(b)[:w].ljust(w, "0"))
        return b
    if name in ("FIXED", "BINARY"):
        return int(num(0))
    if name in ("FLOAT", "DECIMAL"):
        return float(num(0))
    if name in ("HBOUND", "LBOUND", "DIM"):
        ref = node.args[0]
        if not isinstance(ref, N.Ref):
            raise PLIError("%s requires an array argument" % name)
        arr = env.lookup(ref.name)
        if not isinstance(arr, PLIArray):
            raise PLIError("%s: %s is not an array" % (name, ref.name))
        dim = int(to_number(interp.eval(node.args[1], env))) \
            if len(node.args) > 1 else 1
        lo, hi = arr.bounds[dim - 1]
        if name == "HBOUND":
            return hi
        if name == "LBOUND":
            return lo
        return hi - lo + 1
    if name == "LEFT":
        text, n = s(0), int(num(1))
        pad = s(2)[0] if len(args) > 2 and s(2) else " "
        return text[:n].ljust(n, pad)
    if name == "RIGHT":
        text, n = s(0), int(num(1))
        pad = s(2)[0] if len(args) > 2 and s(2) else " "
        return text[max(0, len(text) - n):].rjust(n, pad)
    if name in ("CENTER", "CENTRE"):
        text, n = s(0), int(num(1))
        pad = s(2)[0] if len(args) > 2 and s(2) else " "
        return text[:n].center(n, pad) if len(text) > n \
            else text.center(n, pad)
    if name == "REVERSE":
        r = s(0)[::-1]
        return BitStr(r) if isinstance(args[0], BitStr) else r
    if name == "SEARCH":
        text, chars = s(0), s(1)
        start = int(num(2)) if len(args) > 2 else 1
        for i in range(max(start - 1, 0), len(text)):
            if text[i] in chars:
                return i + 1
        return 0
    if name in ("SEARCHR", "VERIFYR"):
        text, chars = s(0), s(1)
        start = int(num(2)) if len(args) > 2 else len(text)
        want_in = name == "SEARCHR"
        for i in range(min(start, len(text)) - 1, -1, -1):
            if (text[i] in chars) == want_in:
                return i + 1
        return 0
    if name == "TALLY":
        sub = s(1)
        return s(0).count(sub) if sub else 0
    if name == "HIGH":
        return chr(0xFF) * int(num(0))
    if name == "LOW":
        return chr(0x00) * int(num(0))
    if name == "BOOL":
        x, y = to_bits(args[0]), to_bits(args[1])
        tbl = str(to_bits(args[2])).ljust(4, "0")[:4]
        n = max(len(x), len(y), 1)
        x, y = str(x).ljust(n, "0"), str(y).ljust(n, "0")
        return BitStr("".join(tbl[2 * (c1 == "1") + (c2 == "1")]
                              for c1, c2 in zip(x, y)))
    if name == "STRING":
        v = args[0]
        if isinstance(v, PLIStructure):
            return "".join(to_string(x) for x in v.leaf_values())
        if isinstance(v, PLIArray):
            return "".join(to_string(x) for x in v.data)
        return to_string(v)
    if name in ("SUM", "PROD"):
        v = args[0]
        if isinstance(v, PLIArray):
            vals = [to_number(x) for x in v.data]
        elif isinstance(v, PLIStructure):
            vals = [to_number(x) for x in v.leaf_values()]
        else:
            vals = [to_number(v)]
        try:
            r = 0 if name == "SUM" else 1
            for x in vals:
                if isinstance(r, FixedDec) and isinstance(x, float) \
                        or isinstance(x, FixedDec) and isinstance(r, float):
                    r, x = float(r), float(x)
                r = (r + x) if name == "SUM" else (r * x)
            return r
        except FixedOverflow as e:
            raise PLICondition("FIXEDOVERFLOW", str(e))
    if name in ("ADD", "SUBTRACT", "MULTIPLY", "DIVIDE"):
        a, b = num(0), num(1)
        p_ = int(num(2)) if len(args) > 2 else 15
        q_ = int(num(3)) if len(args) > 3 else 0
        inexact = isinstance(a, (float, complex)) \
            or isinstance(b, (float, complex))
        if not inexact:
            a, b = FixedDec.coerce(a), FixedDec.coerce(b)
        try:
            if name == "ADD":
                r = a + b
            elif name == "SUBTRACT":
                r = a - b
            elif name == "MULTIPLY":
                r = a * b
            else:
                if b == 0:
                    raise PLICondition("ZERODIVIDE",
                                       "zero divisor in DIVIDE")
                r = a / b
            return convert(r, Decl("FIXED", prec=(p_, q_)))
        except (FixedOverflow, ZeroDivisionError) as e:
            if isinstance(e, ZeroDivisionError):
                raise PLICondition("ZERODIVIDE", "zero divisor in DIVIDE")
            raise PLICondition("FIXEDOVERFLOW", str(e))
    if name == "RANDOM":
        import random as _random
        if args:
            _random.seed(num(0))
        return _random.random()
    if name == "DATETIME":
        return time.strftime("%Y%m%d%H%M%S") + "000"
    if name == "REAL":
        return complex(num(0)).real
    if name == "IMAG":
        return complex(num(0)).imag
    if name == "CONJG":
        return complex(num(0)).conjugate()
    if name == "COMPLEX":
        return complex(float(num(0)), float(num(1)) if len(args) > 1 else 0.0)
    if name == "COMPLETION":
        if not isinstance(args[0], EventValue):
            raise PLIError("COMPLETION needs an EVENT variable")
        return BitStr("1" if args[0].complete else "0")
    if name == "STATUS":
        if not isinstance(args[0], EventValue):
            raise PLIError("STATUS needs an EVENT variable")
        return args[0].status
    if name == "NULL":
        return Pointer(None)
    if name == "ADDR":
        ref = node.args[0]
        if not isinstance(ref, N.Ref):
            raise PLIError("ADDR requires a simple variable reference")
        entry = env.lookup(ref.name) or interp._search_member(ref.name, env)
        if entry is None:
            raise PLIError("ADDR(%s): unknown variable" % ref.name)
        return Pointer(entry)
    if name == "ALLOCATION":
        ref = node.args[0]
        entry = env.lookup(ref.name) if isinstance(ref, N.Ref) else None
        if isinstance(entry, Controlled):
            return len(entry.stack)
        return 0
    if name == "UNSPEC":
        return unspec_bits(args[0])
    if name == "ONCODE":
        return interp.current_cond.code if interp.current_cond else 0
    if name == "ONCHAR":
        return interp.current_cond.char if interp.current_cond else ""
    if name == "ONSOURCE":
        return interp.current_cond.source if interp.current_cond else ""
    if name == "DATE":
        return time.strftime("%y%m%d")
    if name == "TIME":
        return time.strftime("%H%M%S") + "000"
    raise PLIError("builtin %s not implemented" % name)


# ---- convenience API -------------------------------------------------------------

def run_source(source, stdin=None, stdout=None, include_dir="."):
    Interpreter(stdin=stdin, stdout=stdout).run(source, include_dir)


def run_file(path, stdin=None, stdout=None):
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    run_source(source, stdin=stdin, stdout=stdout,
               include_dir=os.path.dirname(os.path.abspath(path)))
