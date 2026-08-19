"""DL/I preprocessor: intercepts CALL PLITDLI(...) statements.

Real DL/I precompilers rewrite ``CALL PLITDLI(...)`` into a call against a
generated PCB list; the actual database access happens at run time in the
DL/I runtime library.  This module plays that runtime-library role for a
single, simplified IRIS Global acting as the hierarchical database (see
``IRISDLI`` below, adapted from the reference ``iris_dli.py``).

The calling convention this module understands is the classic one:

    CALL PLITDLI(function-count, call-code, pcb-ptr, io-area
                 [, ssa-1 [, ssa-2 [, ...]]]);

``function-count`` (the argument count) and ``pcb-ptr`` are accepted for
source compatibility but are not needed here: this interpreter only ever
talks to one database / one PCB, so both arguments are ignored.  Each SSA
is a CHARACTER value; either a bare segment name (used as-is, e.g.
``'CUSTOMER'``) or a qualified SSA of the form ``NAME(FIELD=VALUE)``, in
which case VALUE is used as the subscript (FIELD is documentation only —
the underlying Global is positional, not field-addressed).

Supported call-codes: GU, GN, GNP, GHU, GHN, GHNP, ISRT, DLET, REPL.
"""
import getpass
import os
from dataclasses import dataclass
from typing import Any, Optional


class DLIError(Exception):
    pass


@dataclass
class DLIResult:
    call: str
    status: str
    subscripts: Optional[tuple] = None
    value: Any = None
    message: str = ""

    @property
    def found(self):
        return self.status == "OK"


class IRISDLI:
    """
    Small educational IMS DL/I-style interface over an IRIS Global.

    Supported:
        GU    Get Unique
        GN    Get Next
        GNP   Get Next within Parent
        GHU   Get Hold Unique
        GHN   Get Hold Next
        GHNP  Get Hold Next within Parent
        ISRT  Insert
        DLET  Delete (requires a prior Get Hold call)
        REPL  Replace (requires a prior Get Hold call)

    The IRIS Global is treated as a hierarchical database.
    """

    def __init__(self, db, global_name="^CUSTOMER", lock_timeout=5):
        self.db = db
        self.global_name = (
            global_name if global_name.startswith("^")
            else "^" + global_name
        )

        self.lock_timeout = lock_timeout

        # Current DL/I position
        self.cursor = None

        # Nodes currently held
        self.held = []

    # ------------------------------------------------------------
    # IRIS Global helpers
    # ------------------------------------------------------------

    def _g(self):
        return self.global_name

    def _get(self, subscripts):
        # The IRIS driver's get() returns None (not "") for a node with
        # no direct value (unset leaf, or a branch node with only
        # descendants); normalize so "" stays the single "no value"
        # sentinel, as it already is for _next_subscript below.
        value = self.db.get(self._g(), *subscripts)
        return "" if value is None else value

    def _next_subscript(self, prefix, previous=""):
        result = self.db.nextSubscript(
            False,          # forward direction
            self._g(),
            *prefix,
            previous
        )

        # nextSubscript returns None (not "") when there is no
        # next subscript, so normalize it here for the rest of
        # the code, which uses "" as the "no more" sentinel.
        return "" if result is None else result

    def _children(self, prefix):
        result = []

        previous = ""

        while True:
            child = self._next_subscript(prefix, previous)

            if child == "":
                break

            result.append(child)
            previous = child

        return result

    def _exists(self, subscripts):
        """
        A Global node exists if it has a value or descendants.
        """

        value = self._get(subscripts)

        if value != "":
            return True

        return len(self._children(subscripts)) > 0

    # ------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------

    def _first_child(self, subscripts):

        child = self._next_subscript(
            subscripts,
            ""
        )

        if child == "":
            return None

        return tuple(subscripts) + (child,)

    def _next_sibling(self, subscripts):

        if not subscripts:
            return None

        parent = subscripts[:-1]
        current = subscripts[-1]

        nxt = self._next_subscript(
            parent,
            current
        )

        if nxt == "":
            return None

        return parent + (nxt,)

    def _next_depth_first(self, subscripts):

        # First descend into children
        child = self._first_child(subscripts)

        if child is not None:
            return child

        # Otherwise find next sibling
        current = tuple(subscripts)

        while current:

            sibling = self._next_sibling(current)

            if sibling is not None:
                return sibling

            current = current[:-1]

        return None

    def _set_cursor(self, subscripts, call):

        self.cursor = tuple(subscripts)

        return DLIResult(
            call=call,
            status="OK",
            subscripts=self.cursor,
            value=self._get(self.cursor)
        )

    # ------------------------------------------------------------
    # GU - Get Unique
    # ------------------------------------------------------------

    def gu(self, subscripts):

        subscripts = tuple(subscripts)

        if not self._exists(subscripts):

            return DLIResult(
                "GU",
                "GE",
                subscripts,
                message="Segment not found"
            )

        return self._set_cursor(
            subscripts,
            "GU"
        )

    # ------------------------------------------------------------
    # GN - Get Next
    # ------------------------------------------------------------

    def gn(self):

        if self.cursor is None:

            first = self._next_subscript(
                (),
                ""
            )

            if first == "":
                return DLIResult(
                    "GN",
                    "GB",
                    message="Database is empty"
                )

            return self._set_cursor(
                (first,),
                "GN"
            )

        nxt = self._next_depth_first(
            self.cursor
        )

        if nxt is None:

            return DLIResult(
                "GN",
                "GB",
                message="End of database"
            )

        return self._set_cursor(
            nxt,
            "GN"
        )

    # ------------------------------------------------------------
    # GNP - Get Next within Parent
    # ------------------------------------------------------------

    def gnp(self, parent=None):

        # Explicit parent:
        # return first child
        if parent is not None:

            parent = tuple(parent)

            if not self._exists(parent):

                return DLIResult(
                    "GNP",
                    "GE",
                    parent,
                    message="Parent not found"
                )

            first = self._next_subscript(
                parent,
                ""
            )

            if first == "":

                return DLIResult(
                    "GNP",
                    "GB",
                    parent,
                    message="Parent has no children"
                )

            return self._set_cursor(
                parent + (first,),
                "GNP"
            )

        # No explicit parent:
        # return next sibling of current position
        if self.cursor is None:

            return DLIResult(
                "GNP",
                "GP",
                message="No current position"
            )

        nxt = self._next_sibling(
            self.cursor
        )

        if nxt is None:

            return DLIResult(
                "GNP",
                "GB",
                message="No more segments within parent"
            )

        return self._set_cursor(
            nxt,
            "GNP"
        )

    # ------------------------------------------------------------
    # HOLD support
    # ------------------------------------------------------------

    def _hold(self, subscripts):

        self.db.lock(
            "",
            self.lock_timeout,
            self._g(),
            *subscripts
        )

        if subscripts not in self.held:
            self.held.append(subscripts)

    def release_hold(self, subscripts):

        subscripts = tuple(subscripts)

        if subscripts in self.held:

            self.db.unlock(
                "",
                self._g(),
                *subscripts
            )

            self.held.remove(subscripts)

    def release_all_holds(self):

        self.db.releaseAllLocks()

        self.held.clear()

    # ------------------------------------------------------------
    # GHU - Get Hold Unique
    # ------------------------------------------------------------

    def ghu(self, subscripts):

        subscripts = tuple(subscripts)

        if not self._exists(subscripts):

            return DLIResult(
                "GHU",
                "GE",
                subscripts,
                message="Segment not found"
            )

        try:

            self._hold(
                subscripts
            )

        except Exception as exc:

            return DLIResult(
                "GHU",
                "NOLOCK",
                subscripts,
                message=str(exc)
            )

        return self._set_cursor(
            subscripts,
            "GHU"
        )

    # ------------------------------------------------------------
    # GHN - Get Hold Next
    # ------------------------------------------------------------

    def ghn(self):

        if self.cursor is None:

            first = self._next_subscript(
                (),
                ""
            )

            if first == "":

                return DLIResult(
                    "GHN",
                    "GB",
                    message="Database is empty"
                )

            nxt = (first,)

        else:

            nxt = self._next_depth_first(
                self.cursor
            )

        if nxt is None:

            return DLIResult(
                "GHN",
                "GB",
                message="End of database"
            )

        try:

            self._hold(
                nxt
            )

        except Exception as exc:

            return DLIResult(
                "GHN",
                "NOLOCK",
                nxt,
                message=str(exc)
            )

        return self._set_cursor(
            nxt,
            "GHN"
        )

    # ------------------------------------------------------------
    # GHNP - Get Hold Next within Parent
    # ------------------------------------------------------------

    def ghnp(self, parent=None):

        result = self.gnp(parent)

        if not result.found:

            result.call = "GHNP"

            return result

        try:

            self._hold(
                result.subscripts
            )

        except Exception as exc:

            return DLIResult(
                "GHNP",
                "NOLOCK",
                result.subscripts,
                message=str(exc)
            )

        result.call = "GHNP"

        return result

    # ------------------------------------------------------------
    # ISRT - Insert
    # ------------------------------------------------------------

    def isrt(self, subscripts, value):

        subscripts = tuple(subscripts)

        if self._exists(subscripts):

            return DLIResult(
                "ISRT",
                "II",
                subscripts,
                message="Segment already exists"
            )

        self.db.set(
            value,
            self._g(),
            *subscripts
        )

        return self._set_cursor(
            subscripts,
            "ISRT"
        )

    # ------------------------------------------------------------
    # DLET - Delete
    # ------------------------------------------------------------

    def dlet(self):

        if self.cursor is None:

            return DLIResult(
                "DLET",
                "GP",
                message="No current position"
            )

        if self.cursor not in self.held:

            return DLIResult(
                "DLET",
                "NOTHELD",
                self.cursor,
                message="Segment not held; use a Get Hold call first"
            )

        deleted = self.cursor

        self.db.kill(
            self._g(),
            *deleted
        )

        self.release_hold(deleted)

        return DLIResult(
            "DLET",
            "OK",
            deleted
        )

    # ------------------------------------------------------------
    # REPL - Replace
    # ------------------------------------------------------------

    def repl(self, value):

        if self.cursor is None:

            return DLIResult(
                "REPL",
                "GP",
                message="No current position"
            )

        if self.cursor not in self.held:

            return DLIResult(
                "REPL",
                "NOTHELD",
                self.cursor,
                message="Segment not held; use a Get Hold call first"
            )

        self.db.set(
            value,
            self._g(),
            *self.cursor
        )

        return self._set_cursor(
            self.cursor,
            "REPL"
        )


# ================================================================
# Connection
# ================================================================

def connect(host=None, port=None, namespace=None, username=None,
            password=None):
    """Open an IRIS connection, taking each parameter from an explicit
    argument, then a PLI_DLI_* environment variable, then (for the
    credentials only) an interactive prompt."""
    import iris  # imported lazily: only needed when DL/I is actually used

    def _first(value, env_name, default=None):
        # "" is a legitimate value (e.g. an empty IRIS username), so
        # check for "not provided" with `is None`, not truthiness.
        if value is not None:
            return value
        env_value = os.environ.get(env_name)
        return env_value if env_value is not None else default

    host = _first(host, "PLI_DLI_HOST", "localhost")
    port = int(_first(port, "PLI_DLI_PORT", "1972"))
    namespace = _first(namespace, "PLI_DLI_NAMESPACE", "SAMPLES")
    username = _first(username, "PLI_DLI_USERNAME")
    if username is None:
        username = input("IRIS username: ")
    password = _first(password, "PLI_DLI_PASSWORD")
    if password is None:
        password = getpass.getpass("IRIS password: ")

    conn = iris.createConnection(host, port, namespace, username, password)
    db = iris.createIRIS(conn)

    return conn, db


# ================================================================
# SSA parsing
# ================================================================

def _coerce(text):
    text = text.strip()
    if text and (text.isdigit()
                 or (text[0] in "+-" and text[1:].isdigit())):
        return int(text)
    return text


def ssa_subscript(raw):
    """Reduce one SSA CHARACTER value to a Global subscript.

    A bare SSA ('CUSTOMER') is used as-is; a qualified SSA
    ('CUSTOMER(CUSTNO=1001)') contributes the value side of its
    relational qualification, since the underlying Global is
    positional rather than field-addressed."""
    s = raw.strip()
    if "(" in s and s.endswith(")"):
        _name, qual = s.split("(", 1)
        qual = qual[:-1]
        for op in ("¬=", "^=", "~=", ">=", "<=", "=", ">", "<"):
            if op in qual:
                return _coerce(qual.split(op, 1)[1])
        return _coerce(qual)
    return _coerce(s)


# ================================================================
# PLITDLI call bridge
# ================================================================

_GET_CALLS = {"GU", "GN", "GNP", "GHU", "GHN", "GHNP"}
_PARENT_CALLS = {"GNP", "GHNP"}


class DLIBridge:
    """Executes one CALL PLITDLI(...) invocation's worth of work
    against an IRISDLI instance."""

    def __init__(self, dli):
        self.dli = dli

    def execute(self, call_code, io_value, ssas):
        method = call_code.strip().upper()
        subs = tuple(ssa_subscript(s) for s in ssas)

        if method == "GU":
            result = self.dli.gu(subs)
        elif method == "GHU":
            result = self.dli.ghu(subs)
        elif method == "GN":
            result = self.dli.gn()
        elif method == "GHN":
            result = self.dli.ghn()
        elif method in _PARENT_CALLS:
            parent = subs if subs else None
            result = (self.dli.gnp(parent) if method == "GNP"
                       else self.dli.ghnp(parent))
        elif method == "ISRT":
            result = self.dli.isrt(subs, io_value)
        elif method == "DLET":
            result = self.dli.dlet()
        elif method == "REPL":
            result = self.dli.repl(io_value)
        else:
            raise DLIError("unsupported DL/I call-code %r" % call_code)

        value = result.value if result.found and method in _GET_CALLS \
            else None

        return result.status, value
