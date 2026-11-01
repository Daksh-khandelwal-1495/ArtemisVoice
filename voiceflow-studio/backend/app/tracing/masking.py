"""Masking for exported traces / evidence: keyed sensitive fields *and* free text.

``mask_payload`` (keys) misses a phone number spoken inside a transcript ("my number is
98765 43210"). :class:`Masker` collects every raw value of a sensitive field seen in the
session and also redacts it inside any string, tolerating spaces / dashes / dots between
characters and ignoring case.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from app.api.common import mask_payload, mask_value


def _compact(v: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(v))


def collect_sensitive_values(obj: Any, sensitive: set[str], out: set[str] | None = None) -> set[str]:
    """Walk ``obj`` and collect raw values stored under sensitive keys (or
    ``{field|field_name: <sensitive>, value: v}`` dicts)."""
    out = set() if out is None else out
    if not sensitive:
        return out
    if isinstance(obj, dict):
        named = obj.get("field") if isinstance(obj.get("field"), str) else obj.get("field_name")
        for k, v in obj.items():
            if (k in sensitive or (k in ("value", "raw") and named in sensitive)) and not isinstance(v, (dict, list)):
                if v is not None and "*" not in str(v):
                    out.add(str(v))
            else:
                collect_sensitive_values(v, sensitive, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_sensitive_values(v, sensitive, out)
    return out


class Masker:
    def __init__(self, sensitive: set[str], values: Iterable[str] = ()):
        self.sensitive = set(sensitive or ())
        self._patterns: list[tuple[re.Pattern, str]] = []
        seen: set[str] = set()
        for v in sorted({_compact(x) for x in values}, key=len, reverse=True):
            if len(v) < 4 or v.lower() in seen:
                continue
            seen.add(v.lower())
            pat = r"[\s\-.]*".join(re.escape(c) for c in v)
            self._patterns.append((re.compile(rf"(?<![A-Za-z0-9]){pat}(?![A-Za-z0-9])", re.IGNORECASE), mask_value(v)))

    @property
    def active(self) -> bool:
        return bool(self.sensitive)

    def text(self, s: str) -> str:
        for pat, repl in self._patterns:
            s = pat.sub(repl, s)
        return s

    def __call__(self, obj: Any) -> Any:
        """Mask keyed sensitive values, then redact known raw values inside strings.

        Artifact dicts (``uri``/``sha256``) are left intact so they stay verifiable."""
        if not self.sensitive:
            return obj
        return self._walk(mask_payload(obj, self.sensitive))

    def _walk(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            if isinstance(obj.get("uri"), str) and obj["uri"].startswith("artifact://") and "sha256" in obj:
                return obj
            return {k: self._walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._walk(v) for v in obj]
        if isinstance(obj, str):
            return self.text(obj)
        return obj
