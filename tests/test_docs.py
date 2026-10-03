"""Docs stay complete: every setting the app reads is documented for deployment."""

from __future__ import annotations

from pathlib import Path

from shipops.config import Settings

ROOT = Path(__file__).resolve().parents[1]

# Read outside shipops.config: by the mock carrier, by the frontend build, or set by the host.
EXTRA_ENV = {"MOCK_CLOCK", "MOCK_TIMEOUT_DELAY_S", "NEXT_PUBLIC_API_URL", "PORT"}


def test_every_env_var_is_documented_in_deploy_md() -> None:
    deploy = (ROOT / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
    names = {name.upper() for name in Settings.model_fields} | EXTRA_ENV
    missing = sorted(n for n in names if f"`{n}`" not in deploy)
    assert not missing, f"Document these in docs/DEPLOY.md: {missing}"


def test_env_example_has_every_secret_and_no_values() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for secret in ("GROQ_API_KEY", "GEMINI_API_KEY"):
        assert f"\n{secret}=\n" in example  # present, empty


def test_license_and_readme_basics() -> None:
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "### In 3 commands" in readme and "docker compose up --build" in readme
    assert "docs/demo/demo.gif" in readme
