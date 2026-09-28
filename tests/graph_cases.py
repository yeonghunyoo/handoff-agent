"""그래프 계층 — LangGraph 상태기계가 tools.py 를 끌고 한 사이클을 도는지. server/.venv 의 python 으로 돈다.

  과제 멈춤(task) 은 submit 으로, 승인 멈춤(approval) 은 approver 로만 재개된다.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, HERE)

try:
    import langgraph  # noqa: F401
except ImportError:
    print("langgraph 가 없어 그래프 계층 검사를 건너뛴다 — server/.venv 로 실행한다.")
    sys.exit(0)

import cases  # noqa: E402
from handoff import graph, tools, util  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  ok  " if cond else "  FAIL") + " " + name + ("" if cond else f"  — {detail}"))
    if not cond:
        FAILS.append(name)


def task(r):
    return ((r or {}).get("pending") or {}).get("task")


def scenario_interactive():
    """스킬이 쓰는 경로 — 빌더는 클라이언트가 띄우고(과제), 승인은 approver."""
    print("scenario: interactive (task ↔ submit · approval ↔ approver)")
    root = cases.make_repo()
    pkg = cases.make_package(os.path.join(os.path.dirname(root), "pkg"))
    r = graph.advance(root)
    check("시작: import 과제", task(r) == "import_design" and r["phase"] == "import", r)
    check("승인 아닌데 approver 줘도 과제 그대로", task(graph.advance(root, approver=cases.APPROVE)) == "import_design")
    r = graph.submit(root, {"path": pkg})
    check("import → spec 과제 (확정 불필요한 패키지)", task(r) == "spec" and r["phase"] == "spec", r)
    check("spec 과제에 힌트·남은 항목", r["pending"].get("hints") and r["pending"].get("remaining"), r["pending"])
    r = graph.submit(root, {"platforms": ["ios", "android"]})
    check("부분 답 → 다시 spec 과제", task(r) == "spec", r)
    r = graph.submit(root, {k: v for k, v in cases.SPEC.items() if k != "platforms"})
    check("스펙 완성 → api 과제", task(r) == "api_submit" and r["phase"] == "api", r)
    r = graph.submit(root, {"openapi": "nope: 1"})
    check("깨진 openapi → 거부 사유와 함께 다시 api 과제", task(r) == "api_submit" and "직전 제출 거부" in r["pending"]["message"], r)
    r = graph.submit(root, {"openapi": cases.OPENAPI})
    check("api → review 승인 대기", r.get("pending_human") and r["action"] == "review" and "지문" in r["approval_prompt"], r)
    r = graph.submit(root, {"approved": True})
    check("승인 대기 중 submit 거부", not r["ok"] and "승인" in r["message"], r)
    r = graph.advance(root)
    check("approver 없이 advance → pending_human + 터미널 명령", not r["ok"] and r["pending_human"] and "run.py run" in r["message"], r)
    r = graph.advance(root, approver=cases.REJECT)
    check("반려 → fix_after_rejection 과제", task(r) == "fix_after_rejection" and "빠진 라우트" in r["pending"]["message"], r)
    r = graph.submit(root, {"openapi": cases.OPENAPI})
    check("재제출 → 다시 승인 대기", r.get("pending_human") and r["action"] == "review", r)
    r = graph.advance(root, approver=cases.APPROVE)
    check("승인 → build → run_builder 과제 (직렬: backend 하나)", task(r) == "run_builder" and r["pending"]["roles"] == ["backend"]
          and "handoff build — role: backend" in r["pending"]["prompts"]["backend"], r)
    seen = []
    for _ in range(4):
        if task(r) != "run_builder":
            break
        role = r["pending"]["roles"][0]
        seen.append(role)
        cases.implement(root, roles=(role,))
        rep = tools.report(root, role, cases.report())
        assert rep["ok"], rep["message"]
        r = graph.submit(root, {})
    check("직렬 착수 순서 backend → ios → android", seen == ["backend", "ios", "android"], seen)
    check("마지막 report 뒤 verify 자동 → ship 승인 대기", r.get("pending_human") and r["action"] == "ship", r)
    r = graph.advance(root, approver=cases.APPROVE)
    check("ship 승인 → done · 머지", r["ok"] and r["phase"] == "done" and not r.get("pending"), r)
    st = util.read_state(root)
    check("runtime_pending 없음 (빌드 성공 자기신고)", st["runtime_pending"] == [] and st["phase"] == "done", st.get("runtime_pending"))
    r = graph.advance(root)
    check("done 에서 advance 는 끝", r["ok"] and r["phase"] == "done" and "완료" in r["message"], r)
    check("체크포인트 파일", os.path.isfile(util.ho(root, graph.DB)))


def scenario_seed_and_command():
    """CLI 가 쓰는 경로 — handoff.spec.json 이 인터뷰를 대신하고, builder.command 로 서버가 빌더를 직접 띄운다 (병렬)."""
    print("scenario: seed spec + builder.command (parallel)")
    root = cases.make_repo()
    pkg = cases.make_package(os.path.join(os.path.dirname(root), "pkg"))
    with open(os.path.join(root, graph.SPEC_SEED), "w") as f:
        json.dump(cases.SPEC, f)
    cfg = util.read_json(util.ho(root, util.CONFIG))
    cfg["builder"] = {"command": f"{sys.executable} {os.path.join(HERE, 'fake_builder.py')}", "timeout_sec": 300}
    cfg["dispatch"] = {"mode": "parallel", "order": ["backend", "ios", "android"]}
    util.write_json(util.ho(root, util.CONFIG), cfg)
    r = graph.advance(root)
    r = graph.submit(root, {"path": pkg})
    check("seed 가 인터뷰를 대신 → 바로 api 과제", task(r) == "api_submit" and r["phase"] == "api", r)
    r = graph.submit(root, {"openapi": cases.OPENAPI})
    r = graph.advance(root, approver=cases.APPROVE)
    check("서버가 빌더 셋을 직접 띄움 → verify 자동 → ship 승인 대기", r.get("pending_human") and r["action"] == "ship", r)
    builders = (r.get("last") or {}).get("builders") or []
    check("빌더 실행 기록 3개 · 모두 성공", len(builders) == 3 and all(b["ok"] for b in builders), r.get("last"))
    r = graph.advance(root, approver=cases.APPROVE)
    check("done", r["ok"] and r["phase"] == "done", r)


def scenario_static_only():
    """툴체인 없는 기기 — 빌더가 build.ok=null 로 보고하면 pass_static, ship 은 runtime_pending 을 남긴다."""
    print("scenario: static-only → pass_static · runtime_pending")
    root = cases.make_repo()
    pkg = cases.make_package(os.path.join(os.path.dirname(root), "pkg"))
    with open(os.path.join(root, graph.SPEC_SEED), "w") as f:
        json.dump(cases.SPEC, f)
    graph.advance(root)
    graph.submit(root, {"path": pkg})
    graph.submit(root, {"openapi": cases.OPENAPI})
    r = graph.advance(root, approver=cases.APPROVE)
    while task(r) == "run_builder":
        role = r["pending"]["roles"][0]
        cases.implement(root, roles=(role,))
        rep = cases.report(build={"ok": None, "seconds": 0}, tests={"passed": 0, "failed": 0, "seconds": 0}) \
            if role == "ios" else cases.report()
        assert tools.report(root, role, rep)["ok"]
        r = graph.submit(root, {})
    check("ship 승인 대기 · 판정 pass_static 이 프롬프트에", r.get("pending_human") and "pass_static" in r["approval_prompt"]
          and "런타임 미검증 역할: ios" in r["approval_prompt"], r)
    r = graph.advance(root, approver=cases.APPROVE)
    st = util.read_state(root)
    check("머지 뒤 runtime_pending=[ios]", r["ok"] and r["phase"] == "done" and st["runtime_pending"] == ["ios"], st.get("runtime_pending"))


def main():
    scenario_interactive()
    scenario_seed_and_command()
    scenario_static_only()
    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}: " + ", ".join(FAILS))
        return 1
    print("graph ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
