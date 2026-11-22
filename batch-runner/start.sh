#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
action=${1:-run}
config=${2:-batch.local.json}
if [[ "$action" == "--help" || "$action" == "-h" ]]; then
    echo "用法: ./start.sh [run|check|status|report|requeue] [批次 JSON 路径] [选项]"
    exit 0
fi
env_files=(--env-file ../.env)
if [[ -f ../trajectory-runner/.env ]]; then
    env_files+=(--env-file ../trajectory-runner/.env)
fi
exec uv run --project ../trajectory-runner --locked --extra mcp "${env_files[@]}" \
    python -I -B ../scripts/openai_profile.py batch "$config" \
    python -I -B batchctl.py "$action" '{profile}' "${@:3}"
