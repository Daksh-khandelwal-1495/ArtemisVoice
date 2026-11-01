"""Field coercion + validation for `collect` nodes.

Pure functions only (no I/O). The LLM passes whatever the caller said (often as spoken
text, e.g. "nine eight seven six..." or "five lakh"); we normalise it into a canonical
value and validate it against the field type and its ``FieldValidation`` rules.

Entry point: :func:`validate_field` -> :class:`FieldResult`.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.schemas.flow import CaptureField

__all__ = [
    "FieldResult",
    "validate_field",
    "is_empty",
    "words_to_number",
    "parse_amount",
    "parse_number",
    "parse_phone",
    "parse_pincode",
    "parse_pan",
    "parse_vehicle_reg",
    "parse_email",
    "parse_date",
    "parse_bool",
    "match_enum",
    "spoken_digits",
    "mask_value",
]


@dataclass
class FieldResult:
    ok: bool
    value: Any = None
    error: str | None = None


class FieldError(ValueError):
    """Raised by parsers with a caller-friendly message."""


# --------------------------------------------------------------------------- helpers

_EMPTY_STRINGS = {
    "", "none", "null", "nil", "n/a", "na", "unknown", "not provided", "not given",
    "not sure", "-", "?", "undefined",
}


def is_empty(raw: Any) -> bool:
    """True if the LLM passed nothing meaningful (missing / placeholder)."""
    if raw is None:
        return True
    if isinstance(raw, str) and raw.strip().lower() in _EMPTY_STRINGS:
        return True
    if isinstance(raw, (list, dict)) and not raw:
        return True
    return False


_UNITS = {
    "zero": 0, "oh": 0, "o": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
    # Hinglish basics
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6,
    "che": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10, "bees": 20, "pachas": 50,
    "pachaas": 50,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13,
    "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20, "thirtieth": 30,
}
_SCALES = {
    "hundred": 100, "sau": 100,
    "thousand": 1_000, "k": 1_000, "hazar": 1_000, "hazaar": 1_000, "hajar": 1_000,
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000, "l": 100_000,
    "million": 1_000_000, "millions": 1_000_000, "mn": 1_000_000,
    "crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000, "karod": 10_000_000,
    "billion": 1_000_000_000,
}
_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "to": "2", "too": "2",
    "three": "3", "four": "4", "for": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "niner": "9",
}


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z]+|\d+(?:\.\d+)?", text.lower().replace(",", ""))


def words_to_number(text: str) -> float | None:
    """Convert spoken numbers to a value: "fifty thousand" -> 50000, "two point five" -> 2.5,
    "1.5 lakh" -> 150000, "twenty one" -> 21. Returns None if nothing numeric is found."""
    toks = [t for t in _tokens(text) if t not in {"and", "a", "rupees", "rupee", "rs", "inr", "only"}]
    if not toks:
        return None
    total = 0.0
    current = 0.0
    seen = False
    decimal_mode = False
    decimal_digits = ""
    for tok in toks:
        if re.fullmatch(r"\d+(?:\.\d+)?", tok):
            if decimal_mode:
                decimal_digits += tok.replace(".", "")
            else:
                current += float(tok)
            seen = True
        elif tok == "point":
            decimal_mode = True
        elif tok in _UNITS or tok in _ORDINALS:
            v = _UNITS.get(tok, _ORDINALS.get(tok))
            if decimal_mode:
                decimal_digits += str(v)
            else:
                current += v
            seen = True
        elif tok in _TENS:
            current += _TENS[tok]
            seen = True
        elif tok in _SCALES:
            scale = _SCALES[tok]
            if decimal_mode:
                current += float("0." + decimal_digits) if decimal_digits else 0
                decimal_mode, decimal_digits = False, ""
            if current == 0:
                current = 1
            if scale == 100:
                current *= 100
            else:
                total += current * scale
                current = 0
            seen = True
        else:
            # unknown word: ignore filler words, but stop on clearly non-numeric text
            continue
    if decimal_mode and decimal_digits:
        current += float("0." + decimal_digits)
    if not seen:
        return None
    return total + current


def _clean_number(v: float) -> int | float:
    return int(v) if float(v).is_integer() else round(v, 4)


def parse_number(raw: Any, *, integer: bool = False) -> int | float:
    if isinstance(raw, bool):
        raise FieldError("Please provide a number.")
    if isinstance(raw, (int, float)):
        v = float(raw)
    else:
        s = str(raw).strip().lower().replace(",", "")
        m = re.fullmatch(r"[-+]?\d+(?:\.\d+)?", s)
        if m:
            v = float(s)
        else:
            # "120000 km", "about 45" -> first numeric chunk + scale words
            w = words_to_number(s)
            if w is None:
                raise FieldError("Please provide a number.")
            v = w
    if integer and not float(v).is_integer():
        raise FieldError("Please provide a whole number.")
    return int(v) if integer else _clean_number(v)


def parse_amount(raw: Any) -> int | float:
    """Indian-style amounts: "5 lakh", "2.5 crore", "50k", "fifty thousand", "₹5,00,000"."""
    if isinstance(raw, bool):
        raise FieldError("Please provide an amount.")
    if isinstance(raw, (int, float)):
        v = float(raw)
    else:
        s = str(raw).strip().lower()
        s = s.replace("₹", " ").replace("rs.", " ").replace(",", "")
        s = re.sub(r"(\d)\s*([a-z])", r"\1 \2", s)  # "50k" -> "50 k", "2.5cr" -> "2.5 cr"
        v_opt = words_to_number(s)
        if v_opt is None:
            raise FieldError("Please provide the amount, for example 5 lakh or 50000.")
        v = v_opt
    if v < 0:
        raise FieldError("Amount cannot be negative.")
    return _clean_number(v)


def spoken_digits(raw: Any) -> str:
    """Extract a digit string from spoken/typed input: "nine eight double seven" -> "9877"."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return str(int(raw))
    s = str(raw).lower()
    out: list[str] = []
    repeat = 1
    for tok in re.findall(r"[a-z]+|\d", s):
        if tok.isdigit():
            out.append(tok * repeat)
            repeat = 1
        elif tok in ("double", "dubble"):
            repeat = 2
        elif tok == "triple":
            repeat = 3
        elif tok in _DIGIT_WORDS:
            out.append(_DIGIT_WORDS[tok] * repeat)
            repeat = 1
    return "".join(out)


def parse_phone(raw: Any) -> str:
    s = str(raw).strip()
    digits = spoken_digits(s)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    elif len(digits) == 13 and digits.startswith("091"):
        digits = digits[3:]
    if not re.fullmatch(r"[6-9]\d{9}", digits):
        raise FieldError("Phone number must be a 10-digit Indian mobile number starting with 6, 7, 8 or 9.")
    return digits


def parse_pincode(raw: Any) -> str:
    digits = spoken_digits(raw)
    if not re.fullmatch(r"[1-9]\d{5}", digits):
        raise FieldError("Pincode must be 6 digits and cannot start with 0.")
    return digits


_NATO = {
    "alpha": "A", "alfa": "A", "bravo": "B", "charlie": "C", "delta": "D", "echo": "E",
    "foxtrot": "F", "golf": "G", "hotel": "H", "india": "I", "juliet": "J", "juliett": "J",
    "kilo": "K", "lima": "L", "mike": "M", "november": "N", "oscar": "O", "papa": "P",
    "quebec": "Q", "romeo": "R", "sierra": "S", "tango": "T", "uniform": "U", "victor": "V",
    "whiskey": "W", "whisky": "W", "xray": "X", "yankee": "Y", "zulu": "Z",
}


def _alnum_compact(raw: Any) -> str:
    """Uppercase alphanumerics with separators removed; understands spoken digits/NATO words
    when the input is clearly spelled out word-by-word."""
    s = str(raw).strip()
    words = re.findall(r"[A-Za-z]+|\d+", s)
    # If every word is a single char, digit word or NATO word, treat as spelled-out.
    spelled = len(words) > 1 and all(
        len(w) == 1 or w.isdigit() or w.lower() in _DIGIT_WORDS or w.lower() in _NATO
        or w.lower() in ("double", "triple")
        for w in words
    )
    if spelled:
        out: list[str] = []
        repeat = 1
        for w in words:
            lw = w.lower()
            if lw == "double":
                repeat = 2
                continue
            if lw == "triple":
                repeat = 3
                continue
            if w.isdigit() or len(w) == 1:
                out.append(w.upper() * repeat)
            elif lw in _DIGIT_WORDS:
                out.append(_DIGIT_WORDS[lw] * repeat)
            else:
                out.append(_NATO[lw] * repeat)
            repeat = 1
        return "".join(out)
    return re.sub(r"[^A-Za-z0-9]", "", s).upper()


def parse_pan(raw: Any) -> str:
    v = _alnum_compact(raw)
    if not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", v):
        raise FieldError("PAN must be 10 characters: 5 letters, 4 digits, then 1 letter (e.g. ABCDE1234F).")
    return v


def parse_vehicle_reg(raw: Any) -> str:
    v = _alnum_compact(raw)
    if re.fullmatch(r"[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}", v) or re.fullmatch(r"[0-9]{2}BH[0-9]{4}[A-Z]{1,2}", v):
        return v
    raise FieldError("Vehicle registration should look like MH12AB1234 (state code, RTO number, series, 4 digits).")


def parse_email(raw: Any) -> str:
    s = str(raw).strip().lower()
    if "@" not in s:
        s = re.sub(r"\s+at\s+|\s*\(at\)\s*", "@", s)
    s = re.sub(r"\s+dot\s+|\s*\(dot\)\s*", ".", s)
    s = re.sub(r"\s+underscore\s+", "_", s)
    s = re.sub(r"\s+(dash|hyphen)\s+", "-", s)
    s = s.replace(" ", "")
    if not re.fullmatch(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", s):
        raise FieldError("Please provide a valid email address, like name@example.com.")
    return s


_TRUE = {
    "yes", "y", "yeah", "yep", "yup", "ya", "yah", "sure", "true", "correct", "right", "ok", "okay",
    "haan", "han", "ha", "haa", "haanji", "ji", "jee", "bilkul", "affirmative", "absolutely", "1",
    "definitely", "of course", "theek hai", "thik hai", "sahi", "sahi hai",
}
_FALSE = {
    "no", "n", "nope", "nah", "false", "incorrect", "wrong", "nahi", "nahin", "na", "nai", "0",
    "not really", "negative", "never", "galat",
}


def parse_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    s = re.sub(r"[^a-z0-9 ]", "", str(raw).strip().lower()).strip()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    words = s.split()
    if words and words[0] in _FALSE:
        return False
    if words and words[0] in _TRUE:
        return True
    raise FieldError("Please answer yes or no.")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def match_enum(raw: Any, options: list[str]) -> str:
    """Case-insensitive fuzzy match against options; returns the canonical option."""
    if not options:
        raise FieldError("No options are configured for this field.")
    s = str(raw).strip()
    ns = _norm(s)
    by_norm = {_norm(o): o for o in options}
    if ns in by_norm:
        return by_norm[ns]
    compact = ns.replace(" ", "")
    for k, o in by_norm.items():
        if k.replace(" ", "") == compact:
            return o
    # containment (e.g. "the nexon ev please" -> "Nexon EV"); prefer longest option
    contained = [o for k, o in by_norm.items() if k and re.search(rf"\b{re.escape(k)}\b", ns)]
    if contained:
        return max(contained, key=len)
    rev = [o for k, o in by_norm.items() if ns and re.search(rf"\b{re.escape(ns)}\b", k)]
    if len(rev) == 1:
        return rev[0]
    def ratio(a: str, b: str) -> float:
        return max(difflib.SequenceMatcher(None, a, b).ratio(), difflib.SequenceMatcher(None, b, a).ratio())

    scored = sorted(((ratio(ns, k), o) for k, o in by_norm.items()), reverse=True)
    if scored and scored[0][0] >= 0.75 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.05):
        return scored[0][1]
    raise FieldError(f"Please choose one of: {', '.join(options)}.")


# --------------------------------------------------------------------------- dates

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9,
    "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12,
    "december": 12,
}
_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5,
    "sat": 5, "sunday": 6, "sun": 6,
}


def _safe_date(y: int, m: int, d: int) -> date:
    try:
        return date(y, m, d)
    except ValueError as e:
        raise FieldError("That date does not exist; please give a valid date.") from e


def _day_from_words(text: str) -> int | None:
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", text)
    if m:
        return int(m.group(1))
    n = words_to_number(" ".join(t for t in _tokens(text) if t in _UNITS or t in _TENS or t in _ORDINALS))
    if n and 1 <= n <= 31:
        return int(n)
    return None


def parse_date(raw: Any, *, today: date | None = None, prefer_future: bool = False) -> date:
    """Parse ISO, dd/mm/yyyy (Indian order), "15 August [2025]", "Aug 15", "tomorrow",
    "day after tomorrow", "next monday", "in 3 days", "next week"."""
    today = today or date.today()
    if isinstance(raw, date):
        return raw
    s = str(raw).strip().lower()
    s = re.sub(r"\s+", " ", s)

    if s in ("today", "aaj", "now", "tonight"):
        return today
    if s in ("day after tomorrow", "parso", "parson") or "day after tomorrow" in s:
        return today + timedelta(days=2)
    if s in ("tomorrow", "kal", "tmrw", "tomorow") or re.search(r"\btomorrow\b", s):
        return today + timedelta(days=1)
    if s == "yesterday" or re.search(r"\byesterday\b", s):
        return today - timedelta(days=1)
    m = re.search(r"\bin (\w+(?: \w+)?) (day|days|week|weeks)\b", s)
    if m:
        n = words_to_number(m.group(1))
        if n is not None:
            mult = 7 if m.group(2).startswith("week") else 1
            return today + timedelta(days=int(n) * mult)
    m = re.search(r"\b(\w+) (day|days|week|weeks) (from now|later)\b", s)
    if m:
        n = words_to_number(m.group(1))
        if n is not None:
            mult = 7 if m.group(2).startswith("week") else 1
            return today + timedelta(days=int(n) * mult)
    if re.fullmatch(r"next week", s):
        return today + timedelta(days=7)
    m = re.search(r"\b(?:(next|this|coming) )?(" + "|".join(_WEEKDAYS) + r")\b", s)
    if m and not re.search(r"\d{1,2}[/-]\d", s) and not any(mon in s.split() for mon in _MONTHS):
        target = _WEEKDAYS[m.group(2)]
        delta = (target - today.weekday()) % 7
        if m.group(1) == "next":
            delta = delta or 7
            # "next monday" said on a Saturday usually means the coming one; keep delta < 7 + 7
        elif delta == 0 and m.group(1) == "coming":
            delta = 7
        return today + timedelta(days=delta)

    # ISO yyyy-mm-dd
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    # dd/mm/yyyy or dd-mm-yy (Indian order)
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})(?:[-/.](\d{2,4}))?", s)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = m.group(3)
        if y is None:
            return _resolve_year(d, mo, None, today, prefer_future)
        yi = int(y)
        if yi < 100:
            yi += 2000
        return _safe_date(yi, mo, d)

    # month names: "15 august 2025", "august 15th", "15th of aug"
    month = None
    for tok in re.findall(r"[a-z]+", s):
        if tok in _MONTHS:
            month = _MONTHS[tok]
            break
    if month:
        year_m = re.search(r"\b(19\d{2}|20\d{2})\b", s)
        rest = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", s)
        day = _day_from_words(rest)
        if day is None:
            raise FieldError("Please tell me the day of the month as well.")
        return _resolve_year(day, month, int(year_m.group(1)) if year_m else None, today, prefer_future)

    # bare day of month ("the 15th") -> this month, or next month if past and prefer_future
    m = re.fullmatch(r"(?:the )?(\d{1,2})(?:st|nd|rd|th)?(?: of this month)?", s)
    if m:
        d = int(m.group(1))
        cand = _safe_date(today.year, today.month, d)
        if prefer_future and cand < today:
            y, mo = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
            cand = _safe_date(y, mo, d)
        return cand
    raise FieldError("Please give a date like 15 August, 15/08/2025, or 'tomorrow'.")


def _resolve_year(d: int, mo: int, y: int | None, today: date, prefer_future: bool) -> date:
    if y is not None:
        return _safe_date(y, mo, d)
    cand = _safe_date(today.year, mo, d)
    if prefer_future and cand < today:
        cand = _safe_date(today.year + 1, mo, d)
    return cand


# --------------------------------------------------------------------------- main entry


def _coerce(field: CaptureField, raw: Any, today: date) -> Any:
    t = field.type
    v = field.validation
    if t == "string":
        s = str(raw).strip()
        return s
    if t == "number":
        return parse_number(raw)
    if t == "integer":
        return parse_number(raw, integer=True)
    if t == "amount":
        return parse_amount(raw)
    if t == "boolean":
        return parse_bool(raw)
    if t == "enum":
        return match_enum(raw, v.options or [])
    if t == "date":
        d = parse_date(raw, today=today, prefer_future=v.future_only)
        if v.future_only and d < today:
            raise FieldError("The date must be today or in the future.")
        if v.past_only and d >= today:
            raise FieldError("The date must be in the past.")
        return d.isoformat()
    if t == "phone":
        return parse_phone(raw)
    if t == "email":
        return parse_email(raw)
    if t == "pincode":
        return parse_pincode(raw)
    if t == "pan":
        return parse_pan(raw)
    if t == "aadhaar_last4":
        digits = spoken_digits(raw)
        if not re.fullmatch(r"\d{4}", digits):
            raise FieldError("Please provide exactly the last 4 digits of the Aadhaar number.")
        return digits
    if t == "vehicle_reg":
        return parse_vehicle_reg(raw)
    return raw


def _check_rules(field: CaptureField, value: Any) -> None:
    v = field.validation
    numeric = field.type in ("number", "integer", "amount")
    label = field.label or field.name
    if numeric:
        if v.min is not None and value < v.min:
            raise FieldError(f"{label} must be at least {_clean_number(v.min)}.")
        if v.max is not None and value > v.max:
            raise FieldError(f"{label} must be at most {_clean_number(v.max)}.")
    elif field.type in ("string", "email"):
        if v.min is not None and len(str(value)) < v.min:
            raise FieldError(f"{label} must be at least {int(v.min)} characters.")
        if v.max is not None and len(str(value)) > v.max:
            raise FieldError(f"{label} must be at most {int(v.max)} characters.")
    if v.regex:
        try:
            if not re.fullmatch(v.regex, str(value)):
                raise FieldError(f"{label} is not in the expected format.")
        except re.error:
            pass  # invalid regex is a flow-validator concern; don't block the call
    if field.type != "enum" and v.options:
        # options on non-enum fields act as an allow-list (fuzzy, canonicalised separately)
        pass


def validate_field(field: CaptureField, raw: Any, *, today: date | None = None) -> FieldResult:
    """Coerce ``raw`` per ``field.type`` then apply validation rules."""
    today = today or date.today()
    if is_empty(raw):
        return FieldResult(False, None, f"{field.label or field.name} was not provided.")
    try:
        value = _coerce(field, raw, today)
        if field.type == "string" and value == "":
            raise FieldError(f"{field.label or field.name} was not provided.")
        _check_rules(field, value)
        return FieldResult(True, value, None)
    except FieldError as e:
        return FieldResult(False, None, str(e))
    except Exception as e:  # defensive: never let a parser bug crash a call
        return FieldResult(False, None, f"Could not understand the value ({type(e).__name__}).")


def mask_value(value: Any) -> str:
    """Mask a sensitive value keeping the last 4 characters: "ABCDE1234F" -> "******234F"."""
    if value is None:
        return ""
    s = str(value)
    if len(s) <= 4:
        return "*" * len(s)
    return "*" * (len(s) - 4) + s[-4:]
