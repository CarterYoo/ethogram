"""Share mode: withhold technical detail (how something was done) from text shown to people, keep what was done.

Logs of misbehaving agents contain working methods — encoded payloads, crafted URLs, injected markup, commands,
addresses, secrets. A report for others needs the behaviour, not the recipe. `redact` replaces such spans with a
short bracketed note; plain language passes through. Used by `serve --share` on every API response.
"""
import re

RULES = [
    ("secret", re.compile(r"(?i)\b(?:sk|pk|ghp|gho|xox[bp]|hf)[-_][A-Za-z0-9_-]{12,}|"
                          r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|password|passwd|secret)\s*[:=]\s*"
                          r"[\"']?(?=[A-Za-z0-9_./+-]*\d)[A-Za-z0-9_./+-]{8,}")),
    ("markup", re.compile(r"(?is)<\s*(script|iframe|style|svg|object)\b.*?(?:<\s*/\s*\1\s*>|$)|"
                          r"</?\s*(?:script|iframe|object|embed|svg|img|style|form|meta|link)\b.*?(?:>|$)|"
                          r"javascript\s*:|\bon[a-z]{3,12}\s*=|\{\{.*?\}\}|<!--.*?-->|\{\|.*?\|\}")),
    ("command", re.compile(r"(?im)^\s*(?:\$\s*)?(?:curl|wget|nc|ncat|python3?|bash|sh|powershell|nmap|sqlmap)\s.*$|"
                           r"\b(?:curl|wget|nmap|sqlmap)\s+(?:-|['\"]|https?:).*$|"
                           r"\$\([^)]*\)|`[^`\n]*(?:\|\s*\w|&&|;\s*\w|\$\(|\b(?:curl|wget|sudo|chmod|rm -)\b)[^`\n]*`")),
    ("url", re.compile(r"(?i)(?:https?|ftp|file|data)://[^\s<>\"')\]]*")),
    ("address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b(?:localhost|0x[0-9a-f]{6,})\b", re.I)),
    ("encoded", re.compile(r"[^\s\[\]]*%[0-9A-Fa-f]{2}[^\s\[\]]*|\b[A-Za-z0-9+/_-]{32,}={0,2}(?![A-Za-z0-9])")),
]
NOTE = {"secret": "[secret withheld]", "markup": "[markup withheld]", "command": "[command withheld]",
        "address": "[address withheld]", "encoded": "[encoded text withheld]"}


def _url(m):
    u = m.group(0)
    host = re.match(r"(?i)[a-z]+://([^/?#:]+)", u)
    has_detail = any(c in u for c in "?#%=&") or len(u) > 60
    if not host:
        return "[link withheld]"
    return f"[link to {host.group(1)}{' — details withheld' if has_detail else ''}]"


def redact(text, counts=None):
    if not isinstance(text, str) or not text:
        return text
    for name, rx in RULES:
        def sub(m, name=name):
            if counts is not None:
                counts[name] = counts.get(name, 0) + 1
            return _url(m) if name == "url" else NOTE[name]
        text = rx.sub(sub, text)
    return text


SKIP_KEYS = {"id", "event_id", "ts", "first", "last", "from", "to", "since", "until", "anchor", "kind", "actor",
             "status", "verdict", "severity", "confidence", "type", "src", "dst"}


def redact_obj(obj, counts=None, key=None):
    """Recursively redact the strings of a JSON-like object (identifiers and times are left alone)."""
    if isinstance(obj, str):
        return obj if key in SKIP_KEYS else redact(obj, counts)
    if isinstance(obj, list):
        return [redact_obj(x, counts, key) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_obj(v, counts, k) for k, v in obj.items()}
    return obj

