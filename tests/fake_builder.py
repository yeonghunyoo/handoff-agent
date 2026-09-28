"""가짜 빌더 — graph 의 builder.command 경로 검사용. 서버가 stdin 으로 준 착수 프롬프트를 받고, 환경 변수의 역할을
픽스처대로 구현한 뒤 report 를 낸다. 실제 빌더(claude -p)가 서는 자리다."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "server"))

import cases  # noqa: E402
from handoff import tools  # noqa: E402


def main():
    root, role, wt = os.environ["HANDOFF_ROOT"], os.environ["HANDOFF_ROLE"], os.environ["HANDOFF_WORKTREE"]
    prompt = sys.stdin.read()
    if f"handoff build — role: {role}" not in prompt or os.getcwd() != os.path.realpath(wt):
        print(f"bad kickoff: cwd={os.getcwd()} wt={wt}", file=sys.stderr)
        return 2
    cases.implement(root, roles=(role,))
    r = tools.report(root, role, cases.report())
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
