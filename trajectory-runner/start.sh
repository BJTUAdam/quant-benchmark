#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
action=${1:-start}
default_config=benchmark/finlab.v2.work-v2-t01.local.json
if [[ ! -f "$default_config" ]]; then
    default_config=benchmark/finlab.v2.skills.local.json
fi
config=${2:-$default_config}
if [[ "$action" == "--help" || "$action" == "-h" ]]; then
    echo "用法: ./start.sh [start|prepare|status] [轨迹 JSON 路径]"
    exit 0
fi
env_files=(--env-file ../.env)
if [[ -f .env ]]; then
    env_files+=(--env-file .env)
fi
exec uv run --locked --extra mcp "${env_files[@]}" \
    python -I -B ../scripts/openai_profile.py trajectory "$config" \
    python -I -B benchmark/finlab_v2.py "$action" --config '{profile}'
