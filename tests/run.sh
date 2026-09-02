#!/usr/bin/env bash
# 도구 자신을 검사한다. 인자 없으면 전체 + MCP 계층(server/.venv 필요).
set -u
cd "$(dirname "$0")"
python3 cases.py "$@" || exit 1
if [ $# -eq 0 ]; then
  PY=../server/.venv/bin/python3
  if [ -x "$PY" ]; then "$PY" mcp_cases.py || exit 1
  else echo "server/.venv 가 없어 MCP 계층 검사를 건너뛴다 (python3 server/run.py serve 를 한 번 띄우면 생긴다)"; fi
fi
