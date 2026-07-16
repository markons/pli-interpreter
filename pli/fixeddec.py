"""Precision-exact FIXED DECIMAL(p,q) arithmetic (PL/I F rules, N = 15).

A FixedDec is an integer mantissa with a decimal scale q and precision p.
Result precisions follow the PL/I F fixed-point rules:

  a + b, a - b :  q = max(q1,q2);  p = min(N, max(p1-q1, p2-q2) + q + 1)
  a * b        :  q = q1 + q2;     p = min(N, p1 + p2 + 1)
  a / b        :  p = N;           q = N - ((p1 - q1) + q2)

FIXEDOVERFLOW is raised when a result exceeds N digits; SIZE when an
assignment target's declared precision is too small.
"""

N_DEC = 15


class FixedOverflow(Exception):
    pass


class SizeError(Exception):
    pass


class FixedDec:
    __slots__ = ("mant", "p", "q")

    def __init__(self, mant, p, q):
        self.mant = int(mant)     # value = mant / 10**q
        self.p = min(int(p), N_DEC)
        self.q = int(q)
        if len(str(abs(self.mant))) > N_DEC:
            raise FixedOverflow("FIXEDOVERFLOW: %s exceeds %d digits"
                                % (self, N_DEC))

    # ---- construction ------------------------------------------------------

    @staticmethod
    def from_literal(text):
        """'12.34' -> FixedDec(1234, 4, 2)"""
        s = text.strip()
        neg = s.startswith("-")
        s = s.lstrip("+-")
        if "." in s:
            i, _, f = s.partition(".")
        else:
            i, f = s, ""
        digits = (i + f).lstrip("0") or "0"
        mant = int(i + f or "0")
        return FixedDec(-mant if neg else mant,
                        max(len(digits), len(f) + 1), len(f))

    @staticmethod
    def coerce(v):
        if isinstance(v, FixedDec):
            return v
        if isinstance(v, bool):
            v = int(v)
        if isinstance(v, int):
            return FixedDec(v, len(str(abs(v))) or 1, 0)
        raise TypeError("cannot coerce %r to FIXED DECIMAL" % (v,))

    def rescale(self, q):
        if q >= self.q:
            return self.mant * 10 ** (q - self.q)
        drop = self.q - q
        m = self.mant
        return (abs(m) // 10 ** drop) * (1 if m >= 0 else -1)  # truncate

    def to_precision(self, p, q):
        """Assignment conversion with SIZE checking."""
        m = self.rescale(q)
        if len(str(abs(m))) > p:
            raise SizeError("SIZE: %s does not fit FIXED(%d,%d)"
                            % (self, p, q))
        return FixedDec(m, p, q)

    # ---- arithmetic ---------------------------------------------------------

    def _addsub(self, other, sign):
        o = FixedDec.coerce(other)
        q = max(self.q, o.q)
        p = min(N_DEC, max(self.p - self.q, o.p - o.q) + q + 1)
        m = self.rescale(q) + sign * o.rescale(q)
        return FixedDec(m, p, q)

    def __add__(self, other):
        return self._addsub(other, 1)

    __radd__ = __add__

    def __sub__(self, other):
        return self._addsub(other, -1)

    def __rsub__(self, other):
        return FixedDec.coerce(other)._addsub(self, -1)

    def __mul__(self, other):
        o = FixedDec.coerce(other)
        return FixedDec(self.mant * o.mant,
                        min(N_DEC, self.p + o.p + 1), self.q + o.q)

    __rmul__ = __mul__

    def __truediv__(self, other):
        o = FixedDec.coerce(other)
        if o.mant == 0:
            raise ZeroDivisionError
        q = N_DEC - ((self.p - self.q) + o.q)
        # value = (m1/10^q1) / (m2/10^q2); scale result to q
        num = self.mant * 10 ** (q + o.q)
        den = o.mant * 10 ** self.q
        m = (abs(num) // abs(den)) * (1 if (num >= 0) == (den > 0) else -1)
        return FixedDec(m, N_DEC, max(q, 0))

    def __rtruediv__(self, other):
        return FixedDec.coerce(other).__truediv__(self)

    def __neg__(self):
        return FixedDec(-self.mant, self.p, self.q)

    def __pos__(self):
        return self

    def __abs__(self):
        return FixedDec(abs(self.mant), self.p, self.q)

    def __pow__(self, other):
        if isinstance(other, int) and other >= 0:
            r = FixedDec(1, 1, 0)
            for _ in range(other):
                r = r * self
            return r
        return float(self) ** float(other)

    # ---- conversions / comparison -------------------------------------------

    def __float__(self):
        return self.mant / 10 ** self.q

    def __complex__(self):
        return complex(float(self))

    def __int__(self):
        return self.rescale(0)

    __trunc__ = __int__

    def __round__(self, ndigits=None):
        if ndigits:
            return round(float(self), ndigits)
        if self.q == 0:
            return self.mant
        m, r = divmod(abs(self.mant), 10 ** self.q)
        if 2 * r >= 10 ** self.q:
            m += 1
        return -m if self.mant < 0 else m

    def __bool__(self):
        return self.mant != 0

    def _cmp_key(self):
        return self.mant * 10 ** (N_DEC - self.q)

    def _other_key(self, other):
        if isinstance(other, FixedDec):
            return other._cmp_key()
        if isinstance(other, (int, float)):
            return other * 10 ** N_DEC
        return NotImplemented

    def __eq__(self, other):
        k = self._other_key(other)
        return NotImplemented if k is NotImplemented else self._cmp_key() == k

    def __ne__(self, other):
        r = self.__eq__(other)
        return NotImplemented if r is NotImplemented else not r

    def __lt__(self, other):
        return self._cmp_key() < self._other_key(other)

    def __le__(self, other):
        return self._cmp_key() <= self._other_key(other)

    def __gt__(self, other):
        return self._cmp_key() > self._other_key(other)

    def __ge__(self, other):
        return self._cmp_key() >= self._other_key(other)

    def __hash__(self):
        return hash(float(self))

    def __repr__(self):
        return self.__str__()

    def __str__(self):
        m = abs(self.mant)
        sign = "-" if self.mant < 0 else ""
        if self.q == 0:
            return sign + str(m)
        s = str(m).rjust(self.q + 1, "0")
        return "%s%s.%s" % (sign, s[:-self.q], s[-self.q:])
