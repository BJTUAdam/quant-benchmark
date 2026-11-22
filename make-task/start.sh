#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")/task_generator"
if [[ ! -x .venv/bin/python ]]; then uv venv --python 3.12; fi
if [[ ! -x .venv/bin/finlab ]]; then uv pip install --python .venv/bin/python -e .; fi
run=(uv run --no-sync --env-file ../../.env --env-file .env)
if (($# == 0)); then set -- task generate; fi
case "${1:-} ${2:-}" in
    "task generate"|"task review"|"task doctor")
        exec "${run[@]}" python ../../scripts/openai_profile.py task config/default.json \
            finlab "$@" --config '{profile}' ;;
    *) exec "${run[@]}" finlab "$@" ;;
esac
