#!/usr/bin/env bash
# Start guardrails + scanner in a tmux session named hlg.
cd "$(dirname "$0")"
tmux new-session -d -s hlg -n guard ". .venv/bin/activate && python -m hlg.guardrails"
tmux new-window -t hlg -n scan ". .venv/bin/activate && python -m hlg.scanner"
echo "started: tmux attach -t hlg"
