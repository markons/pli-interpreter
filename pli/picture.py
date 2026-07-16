"""PL/I PICTURE support: validation, editing and numeric value extraction.

Supported picture characters:
  numeric:   9  Z  *  V  ,  .  B  /  S  +  -  $  CR  DB
             (S/+/-/$ static when single, drifting when repeated)
  character: A  X  9

The edited form is stored as a PicStr (a str carrying the numeric value),
so PUT LIST shows the edited text while arithmetic uses the number.
"""


class PictureError(Exception):
    pass


class PicStr(str):
    """Edited picture text that remembers its numeric value."""
    num = 0

    def __new__(cls, text, num=0):
        self = str.__new__(cls, text)
        self.num = num
        return self


_SIGNS = "S+-$"


class Picture:
    def __init__(self, spec):
        self.spec = spec.upper()
        self.tokens = self._tokenize(self.spec)
        self.is_char = any(t in ("A", "X") for t in self.tokens)
        if self.is_char:
            if any(t not in ("A", "X", "9") for t in self.tokens):
                raise PictureError("invalid character picture %r" % spec)
            self.length = len(self.tokens)
            return
        # numeric picture: classify positions
        self.drift_char = None
        for c in _SIGNS:
            if self.tokens.count(c) > 1:
                self.drift_char = c
                break
        v_seen = False
        self.int_digits = 0
        self.frac_digits = 0
        drift_seen = 0
        for t in self.tokens:
            if t == "V":
                if v_seen:
                    raise PictureError("multiple V in picture %r" % spec)
                v_seen = True
            elif t in ("9", "Z", "*"):
                if v_seen:
                    self.frac_digits += 1
                else:
                    self.int_digits += 1
            elif self.drift_char and t == self.drift_char:
                drift_seen += 1
                if drift_seen > 1:      # first drift position holds the symbol
                    if v_seen:
                        self.frac_digits += 1
                    else:
                        self.int_digits += 1
        # display length comes from a trial edit
        self.length = len(self.edit(0))

    @staticmethod
    def _tokenize(spec):
        toks = []
        i = 0
        while i < len(spec):
            if spec[i] in " ":
                i += 1
                continue
            if spec[i:i + 2] in ("CR", "DB"):
                toks.append(spec[i:i + 2])
                i += 2
                continue
            if spec[i] not in "9ZV*,./BSAX+-$":
                raise PictureError("invalid picture character %r" % spec[i])
            toks.append(spec[i])
            i += 1
        return toks

    # ---- character pictures ------------------------------------------------

    def validate_char(self, s):
        s = s[:self.length].ljust(self.length)
        for c, t in zip(s, self.tokens):
            if t == "A" and not (c.isalpha() or c == " "):
                return None
            if t == "9" and not c.isdigit():
                return None
        return s

    # ---- numeric pictures ---------------------------------------------------

    def edit(self, number):
        """Edit a number into the picture; returns the display string."""
        neg = number < 0
        scaled = int(round(abs(number) * 10 ** self.frac_digits))
        digits = str(scaled).rjust(self.int_digits + self.frac_digits, "0")
        if len(digits) > self.int_digits + self.frac_digits:
            raise PictureError("SIZE: %r does not fit picture %s"
                               % (number, self.spec))
        int_part, frac_part = (digits[:self.int_digits],
                               digits[self.int_digits:])

        drift = self.drift_char
        out = []
        di = 0                     # index into int_part digits
        fi = 0                     # index into frac_part digits
        significant = False
        fill = "*" if "*" in self.tokens else " "
        v_seen = False
        drift_positions = []       # output indexes eligible for drift symbol
        first_digit_out = None

        def digit_kind(tok):
            return tok in ("9", "Z", "*") or (drift and tok == drift)

        drift_count = 0
        for t in self.tokens:
            if t == "V":
                v_seen = True
                continue
            if t in ("CR", "DB"):
                out.append("CR" if (neg and t == "CR")
                           else ("DB" if (neg and t == "DB") else "  "))
                continue
            if drift and t == drift:
                drift_count += 1
                if drift_count == 1:
                    # first occurrence: pure symbol position
                    drift_positions.append(len(out))
                    out.append(fill)
                    continue
                # further occurrences act as suppressed digit positions
                d = self._next_digit(int_part, frac_part, v_seen, di, fi)
                if v_seen:
                    fi += 1
                else:
                    di += 1
                if d != "0" or significant or v_seen:
                    significant = True
                    if first_digit_out is None:
                        first_digit_out = len(out)
                    out.append(d)
                else:
                    drift_positions.append(len(out))
                    out.append(fill)
                continue
            if t == "9":
                d = self._next_digit(int_part, frac_part, v_seen, di, fi)
                if v_seen:
                    fi += 1
                else:
                    di += 1
                significant = True
                if first_digit_out is None:
                    first_digit_out = len(out)
                out.append(d)
                continue
            if t in ("Z", "*"):
                d = self._next_digit(int_part, frac_part, v_seen, di, fi)
                if v_seen:
                    fi += 1
                else:
                    di += 1
                if d != "0" or significant or v_seen:
                    significant = True
                    if first_digit_out is None:
                        first_digit_out = len(out)
                    out.append(d)
                else:
                    out.append("*" if t == "*" else " ")
                continue
            if t in (",", ".", "/", "B"):
                ch = " " if t == "B" else t
                out.append(ch if significant else fill)
                continue
            if t in ("S", "+", "-", "$"):     # static sign / currency
                if t == "S":
                    out.append("-" if neg else "+")
                elif t == "+":
                    out.append("+" if not neg else "-")
                elif t == "-":
                    out.append("-" if neg else " ")
                else:
                    out.append("$")
                continue
            raise PictureError("unhandled picture char %r" % t)

        # place drifting symbol just left of the first significant digit
        if drift:
            sym = {"S": ("-" if neg else "+"),
                   "+": ("-" if neg else "+"),
                   "-": ("-" if neg else " "),
                   "$": "$"}[drift]
            spot = None
            for pos in drift_positions:
                if first_digit_out is None or pos < first_digit_out:
                    spot = pos
            if spot is None and drift_positions:
                spot = drift_positions[0]
            if spot is not None and sym != " ":
                out[spot] = sym

        text = "".join(out)
        value = -abs(number) if neg else abs(number)
        return PicStr(text, value)

    def _next_digit(self, int_part, frac_part, v_seen, di, fi):
        if v_seen:
            return frac_part[fi] if fi < len(frac_part) else "0"
        return int_part[di] if di < len(int_part) else "0"

    def assign(self, number):
        """Assignment conversion: number -> PicStr for this picture."""
        if self.is_char:
            raise PictureError("numeric value assigned to character picture")
        return self.edit(number)

    def value(self, text):
        """Numeric value of an edited string (for GET / arithmetic)."""
        if isinstance(text, PicStr):
            return text.num
        s = str(text)
        neg = "CR" in s or "DB" in s or "-" in s
        digits = "".join(c for c in s if c.isdigit())
        if not digits:
            return 0
        n = int(digits)
        v = n / 10 ** self.frac_digits if self.frac_digits else n
        return -v if neg else v
