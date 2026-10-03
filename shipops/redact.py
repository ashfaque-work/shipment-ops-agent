"""Secret redaction for anything written to disk or committed (eval cassettes, results).

Defense in depth: cassettes record at the LLM-abstraction level (parsed replies, never
HTTP headers, URLs or request bodies), so credentials should not reach them at all. This
scrubber guarantees it anyway, and `python -m shipops.redact --check <paths>` lets CI fail
if a file would change.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

REDACTED = "[REDACTED]"

# Dict keys whose values are always secrets (compared case-insensitively).
SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "x-goog-api-key",
        "api-key",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "client_secret",
        "password",
        "secret",
        "token",
        "key",
    }
)

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Authorization header values in free text
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 " + REDACTED),
    # ?key=... / &api_key=... / access_token=... in URLs
    (
        re.compile(r"(?i)((?:^|[?&;\s])(?:key|api_key|apikey|access_token|token)=)[^&\s\"'#]+"),
        r"\1" + REDACTED,
    ),
    # Known provider key formats
    (re.compile(r"\bgsk_[A-Za-z0-9]{20,}"), REDACTED),  # Groq
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), REDACTED),  # Google / Gemini
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), REDACTED),  # OpenAI-style
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),  # AWS access key id
]

_ENV_SECRETS = ("GROQ_API_KEY", "GEMINI_API_KEY", "FEDEX_API_KEY", "FEDEX_SECRET_KEY")


def _configured_secrets() -> list[str]:
    values = [os.environ.get(k, "") for k in _ENV_SECRETS]
    try:  # also the values loaded from .env via settings, if available
        from shipops.config import get_settings

        s = get_settings()
        values += [s.groq_api_key, s.gemini_api_key]
    except Exception:  # pragma: no cover - redaction must never fail because of config
        pass
    return sorted({v for v in values if v and len(v) >= 8}, key=len, reverse=True)


def scrub_text(text: str, secrets: list[str] | None = None) -> str:
    for value in secrets if secrets is not None else _configured_secrets():
        text = text.replace(value, REDACTED)
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def scrub(obj: Any, secrets: list[str] | None = None) -> Any:
    """Return a copy of a JSON-like structure with secrets replaced by [REDACTED]."""
    secrets = _configured_secrets() if secrets is None else secrets
    if isinstance(obj, dict):
        return {
            k: (
                REDACTED
                if isinstance(k, str) and k.lower() in SENSITIVE_KEYS and v not in (None, "")
                else scrub(v, secrets)
            )
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [scrub(v, secrets) for v in obj]
    if isinstance(obj, str):
        return scrub_text(obj, secrets)
    return obj


def scrub_file(path: Path, *, write: bool) -> bool:
    """Scrub one JSON file in place. Returns True if it contained something to redact."""
    original = path.read_text(encoding="utf-8")
    cleaned = scrub(json.loads(original))
    changed = json.loads(original) != cleaned
    if changed and write:
        lines = original.splitlines()
        second = lines[1] if len(lines) > 1 else ""
        indent = (len(second) - len(second.lstrip(" "))) or 1  # keep the file's style
        path.write_text(json.dumps(cleaned, indent=indent) + "\n", encoding="utf-8", newline="\n")
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Redact secrets from JSON files (in place).")
    ap.add_argument("paths", nargs="+", help="files or directories (*.json, recursive)")
    ap.add_argument("--check", action="store_true", help="report only; exit 1 if dirty")
    a = ap.parse_args(argv)
    files = [
        f for p in map(Path, a.paths) for f in ([p] if p.is_file() else sorted(p.rglob("*.json")))
    ]
    dirty = [f for f in files if scrub_file(f, write=not a.check)]
    verb = "would be redacted" if a.check else "redacted"
    print(f"scanned {len(files)} JSON files; {len(dirty)} {verb}")
    for f in dirty:
        print(f"  {f}")
    return 1 if (a.check and dirty) else 0


if __name__ == "__main__":
    sys.exit(main())
