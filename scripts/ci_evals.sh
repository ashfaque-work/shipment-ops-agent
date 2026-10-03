#!/usr/bin/env bash
# Offline evals for CI: no API keys needed.
#  1. deterministic rule-based baseline
#  2. replay of every recorded LLM cassette (--strict: drift or missing recordings fail)
#  3. committed results must be reproduced byte-for-byte
set -euo pipefail

uv run python -m eval.run --llm baseline

shopt -s nullglob
for dir in eval/cassettes/*/; do
  label="$(basename "$dir")"
  echo "== replaying $label"
  uv run python -m eval.run --llm replay --label "$label" --strict
done

if ! git diff --exit-code -- eval/results README.md; then
  echo "Eval results differ from the committed ones. If the change is intended, re-record:"
  echo "  uv run python -m eval.run --llm live --provider <groq|gemini> --model <model>"
  exit 1
fi
