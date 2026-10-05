"""가짜 빌더 — graph 의 builder.command 경로 검사용. 서버가 stdin 으로 준 착수 프롬프트를 받고, 환경 변수의 역할을
픽스처대로 구현한 뒤 report 를 낸다. 실제 빌더(claude -p)가 서는 자리다."""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "server"))

import cases  # noqa: E402
from handoff import tools  # noqa: E402


def main():
    root, role, wt = os.environ["HANDOFF_ROOT"], os.environ["HANDOFF_ROLE"], os.environ["HANDOFF_WORKTREE"]
    prompt = sys.stdin.read()
    t_start = time.time()
    if f"handoff build — role: {role}" not in prompt or os.getcwd() != os.path.realpath(wt):
        print(f"bad kickoff: cwd={os.getcwd()} wt={wt}", file=sys.stderr)
        return 2
    if os.environ.get("HANDOFF_FAKE_SLEEP"):             # 동시 실행 검사용 — 빌더가 일하는 시간을 흉내낸다
        time.sleep(float(os.environ["HANDOFF_FAKE_SLEEP"]))
    cases.implement(root, roles=(role,))
    r = tools.report(root, role, cases.report())
    print(f"fake-builder {role} start={t_start:.3f} end={time.time():.3f} ok={r.get('ok')}")   # 동시 실행 검사가 겹침을 읽는다
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
