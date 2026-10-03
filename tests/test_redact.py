from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.cassette import Cassette
from shipops.redact import REDACTED, main, scrub, scrub_text

FAKE_GROQ = "gsk_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
FAKE_GOOGLE = "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4o5p"


@pytest.mark.parametrize(
    "text,leaked",
    [
        (f"Authorization: Bearer {FAKE_GROQ}", FAKE_GROQ),
        (
            "GET https://generativelanguage.googleapis.com/v1/models?key=abc123secret&alt=json",
            "abc123secret",
        ),
        (f"https://example.com/x?api_key={FAKE_GOOGLE}", FAKE_GOOGLE),
        (f"error for key {FAKE_GOOGLE} in project", FAKE_GOOGLE),
        ("access_token=eyJhbGciOiJIUzI1NiJ9.payload.sig&x=1", "eyJhbGciOiJIUzI1NiJ9"),
        ("Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
    ],
)
def test_scrub_text_patterns(text: str, leaked: str) -> None:
    out = scrub_text(text, secrets=[])
    assert leaked not in out and REDACTED in out


def test_scrub_exact_configured_value() -> None:
    assert (
        scrub_text("my key is s3cr3t-value-123", secrets=["s3cr3t-value-123"])
        == f"my key is {REDACTED}"
    )


def test_scrub_structures_and_sensitive_keys() -> None:
    obj = {
        "headers": {
            "Authorization": "Bearer abcdefghijkl",
            "x-goog-api-key": "zzz",
            "Content-Type": "application/json",
        },
        "usage": {"prompt_tokens": 10},
        "message": {"role": "assistant", "content": "Order 4512 is in transit."},
        "list": [f"url?key={FAKE_GROQ}"],
    }
    out = scrub(obj, secrets=[])
    assert out["headers"]["Authorization"] == REDACTED
    assert out["headers"]["x-goog-api-key"] == REDACTED
    assert out["headers"]["Content-Type"] == "application/json"
    assert out["usage"] == {"prompt_tokens": 10}  # token *counts* are not secrets
    assert out["message"] == obj["message"]  # normal content untouched
    assert FAKE_GROQ not in json.dumps(out)
    assert scrub(out, secrets=[]) == out  # idempotent


def test_cassette_save_scrubs(tmp_path: Path) -> None:
    cas = Cassette(tmp_path / "c.json")
    cas.put(
        "case",
        [
            {
                "request_hash": "h",
                "result": {
                    "message": {"role": "assistant", "content": f"leaked {FAKE_GROQ}"},
                    "provider": "groq",
                    "model": "m",
                },
            }
        ],
        True,
    )
    cas.save()
    text = (tmp_path / "c.json").read_text()
    assert FAKE_GROQ not in text and REDACTED in text


def test_cli_check_and_fix(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = tmp_path / "r.json"
    f.write_text(json.dumps({"reply": f"key {FAKE_GOOGLE}"}, indent=1) + "\n")
    assert main([str(tmp_path), "--check"]) == 1
    assert FAKE_GOOGLE in f.read_text()  # --check never writes
    assert main([str(tmp_path)]) == 0
    assert FAKE_GOOGLE not in f.read_text()
    assert main([str(tmp_path), "--check"]) == 0


def test_committed_eval_artifacts_are_clean() -> None:
    root = Path(__file__).resolve().parents[1] / "eval"
    assert main([str(root / "cassettes"), str(root / "results"), "--check"]) == 0
