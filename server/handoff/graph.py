"""그래프 — LangGraph 상태기계. 흐름은 코드가 끌고, LLM 과 사람은 멈춤 지점(interrupt)에서만 채운다.

  START → check ─phase─▶ import → confirm? · spec → spec_ask? · api · review ✋ → review_fix? · build → dispatch? · verify · ship ✋ → ship_fix? · done
             ▲                                                                                                                  │
             └──────────────────────── 모든 노드는 check 로 돌아온다 (done 만 END) ──────────────────────────────────────────────┘

노드는 tools.py 의 순수 함수를 부른다. 저장 상태(.handoff/state.json)와 flow.current() 의 실측 대조가 여전히 정본이고,
그래프 체크포인트(.handoff/graph.sqlite)는 "어디서 멈췄나" 만 기억한다 — check 가 매번 phase 를 다시 읽으므로 둘이 어긋나면
실측이 이긴다. langgraph 를 import 하는 유일한 파일이다 (mcp 는 server.py 만).

멈춤은 두 종류다:
  kind=approval  사람 승인 (review · ship). 재개 값은 approver 콜백에서만 온다 — MCP 는 elicitation, CLI 는 tty.
                 submit() 으로는 재개되지 않는다 (승인 도구에 approve 류 인자를 두지 않는다는 원칙과 같다).
  kind=task      LLM(또는 사람)이 할 일 — 패키지 경로 · 화면 확정 · 스펙 답 · openapi 초안 · 빌더 실행 · 막힘 해소.
                 submit(payload) 로 재개한다. 과제마다 payload 의 모양이 응답에 적혀 있다.

한 노드 안에 부작용(도구 호출) 뒤 interrupt 를 두지 않는다 — 재개 시 노드가 처음부터 다시 실행되므로 부작용이 중복된다.
그래서 "물어보는 노드" 와 "실행하는 노드" 를 나눈다 (import/confirm · spec/spec_ask · review/review_fix · ship/ship_fix).
"""
import contextlib
import operator
import os
import subprocess
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from . import flow, tools, util

DB = "graph.sqlite"
THREAD = "handoff"
CFG = {"configurable": {"thread_id": THREAD}, "recursion_limit": 500}
SPEC_SEED = util.SPEC_SEED           # 레포 루트 handoff.spec.json — 커밋해 두면 인터뷰 없이 스펙이 채워진다 (빈 항목만 묻는다)
OVERLAYS = ("sheet", "modal", "popover")


class State(TypedDict, total=False):
    root: str
    phase: str
    last: dict                          # 마지막 도구 응답 (요약)
    needs_confirm: bool                 # import 뒤 사람이 화면·컴포넌트를 확정해야 하는가
    dispatch: dict                      # build 가 정한 이번 착수 {roles, prompts} — dispatch 노드가 과제로 넘긴다
    log: Annotated[list, operator.add]


# ─────────────────────────── 멈춤 ───────────────────────────

def _task(name, message, payload_shape, **fields):
    """LLM 과제. 응답에 무엇을 submit 해야 하는지가 적힌다."""
    return interrupt({"kind": "task", "task": name, "message": message, "submit": payload_shape, **fields})


def _approval(action, prompt):
    return interrupt({"kind": "approval", "action": action, "prompt": prompt})


def _fix(r, hint):
    """도구가 거부했다 — 같은 노드를 무한히 돌지 않도록 사람/LLM 에게 넘긴다. payload: {"back": to, "reason"} 또는 {"retry": true}."""
    return _task("fix", f"{r.get('message')}\n{hint}",
                 {"retry": "true — 원인을 밖에서 고친 뒤 다시 시도", "back": "import|spec|api|build — 앞 단계로", "reason": "back 의 사유"})


def _apply_fix(root, payload):
    if isinstance(payload, dict) and payload.get("back"):
        return tools.back(root, payload["back"], payload.get("reason") or "graph fix")
    return {"ok": True, "message": "retry"}


def _slim(r):
    """체크포인트에 남길 응답 요약 — 프롬프트·표 같은 큰 본문은 뺀다."""
    if not isinstance(r, dict):
        return {"ok": bool(r)}
    keep = ("ok", "message", "approved", "verdict", "score", "runtime_pending", "pending", "resume", "roles", "draft", "remaining")
    return {k: r[k] for k in keep if k in r}


# ─────────────────────────── 노드 ───────────────────────────

def n_check(s):
    cfg, st = tools._st(s["root"])
    return {"phase": st["phase"], "log": [f"check:{st['phase']}"]}


def n_import(s):
    root = s["root"]
    st = tools.status(root)
    last = s.get("last") or {}
    payload = _task("import_design",
                    "Claude Design 패키지를 등록한다. 레포 루트의 후보(candidates)가 있으면 사용자에게 확인받고 그 경로를, "
                    "아니면 사용자가 준 zip/tar.gz/standalone HTML/폴더 경로(또는 링크는 스테이징 폴더)를 낸다."
                    + (f"\n직전 시도 실패: {last.get('message')}" if last.get("ok") is False else ""),
                    {"path": "패키지 경로 (필수)", "url": "claude.ai/design 링크 (선택)", "target": "mobile|web|mixed (선택)"},
                    candidates=st.get("candidates") or [])
    args = {k: payload.get(k) for k in ("path", "url", "target") if isinstance(payload, dict) and payload.get(k)}
    r = tools.import_design(root, **args)
    needs = bool(r.get("ok") and not r.get("confirmed")
                 and (any(sc.get("anchor") for sc in r.get("screens", []))
                      or any(c.get("type") in OVERLAYS for c in r.get("components", []))))
    return {"last": _slim(r) | {"screens": r.get("screens"), "components": r.get("components"), "target": r.get("target")},
            "needs_confirm": needs, "log": ["import"]}


def n_confirm(s):
    root = s["root"]
    last = s.get("last") or {}
    payload = _task("confirm_screens",
                    "프로토타입의 상태 분기를 화면 후보로, 오버레이를 컴포넌트로 뽑았다. 사용자에게 목록을 보여 확정받는다. "
                    "고칠 것이 없으면 빈 객체를 내면 그대로 확정된다.",
                    {"screens": "[{id,title,file,anchor,variants?,path?}] (선택 — 없으면 현재 목록 그대로)",
                     "components": "[{id,type,title,anchor}] (선택)", "target": "mobile|web|mixed (선택)"},
                    screens=last.get("screens") or [], components=last.get("components") or [], target=last.get("target"))
    p = payload if isinstance(payload, dict) else {}
    r = tools.import_design(root, screens=p.get("screens") or last.get("screens"), components=p.get("components"),
                            target=p.get("target"))
    return {"last": _slim(r), "needs_confirm": False, "log": ["confirm"]}


def n_spec(s):
    """커밋된 기본값(handoff.spec.json)이 있으면 먼저 넣는다 — 멱등이라 재실행돼도 같다. 남은 항목만 spec_ask 가 묻는다."""
    root = s["root"]
    seed = util.read_json(os.path.join(root, SPEC_SEED))
    if isinstance(seed, dict) and seed:
        r = tools.spec_save(root, seed)
        return {"last": _slim(r) | {"seeded": True}, "log": ["spec:seed"]}
    return {"last": {"ok": True, "draft": True, "seeded": False}, "log": ["spec"]}


def n_spec_ask(s):
    root = s["root"]
    st = tools.status(root)
    payload = _task("spec",
                    "스펙 인터뷰 — 남은 항목만 사용자에게 묻고 답을 낸다 (부분 저장이 누적된다). 인프라는 규모(MAU·DAU)가 먼저다: "
                    "규모가 저장돼야 infra_options 에 후보 조합 4~5개가 실린다. 힌트는 기본값 제안일 뿐 사람이 고른다.",
                    {"platforms": "[ios|android|web]", "stack": "{backend, ios_project?, android_project?, web_project?}",
                     "infra": "{mau, dau | scale, combo | db, auth, hosting, env_vars[], pricing?}"},
                    remaining=[w for w in st.get("warnings", []) if "인터뷰" in w or "인프라" in w] or st.get("warnings", []),
                    hints=st.get("hints"), infra_options=st.get("infra_options"))
    r = tools.spec_save(root, payload if isinstance(payload, dict) else {})
    return {"last": _slim(r), "log": ["spec:ask"]}


def n_api(s):
    root = s["root"]
    last = s.get("last") or {}
    st = tools.status(root)
    payload = _task("api_submit",
                    "design/ 의 화면 HTML · derived/entities.json · navigation.json 을 읽고 각 화면이 필요로 하는 데이터·동작으로 "
                    "openapi.yaml 을 초안한다. operationId 는 lowerCamel (앱이 부르는 ApiRoutes.<operationId>). 자리표시 외 시크릿 금지."
                    + (f"\n직전 제출 거부: {last.get('message')}" if last.get("ok") is False else ""),
                    {"openapi": "openapi.yaml 본문 (문자열)"},
                    screens=(st.get("design") or {}).get("screens"), entities="design/derived/entities.json")
    r = tools.api_submit(root, (payload or {}).get("openapi") if isinstance(payload, dict) else "")
    return {"last": _slim(r), "log": ["api"]}


def n_review(s):
    root = s["root"]
    pre = tools.review(root, None)
    if not pre.get("pending_human"):
        p = _fix(pre, "review 가 열리지 않는다 — 스펙·openapi 를 채우거나 back 으로 돌아간다.")
        return {"last": _slim(_apply_fix(root, p)), "log": ["review:blocked"]}
    decision = _approval("review", pre["approval_prompt"])
    r = tools.review(root, approver=lambda m, i: decision)
    return {"last": _slim(r), "log": [f"review:{'ok' if r.get('approved') else 'rejected'}"]}


def n_review_fix(s):
    root = s["root"]
    last = s.get("last") or {}
    payload = _task("fix_after_rejection",
                    f"계약이 반려됐다: {last.get('message')}\n사유에 맞게 고친다 — openapi 면 api_submit(또는 back to=api), "
                    "스펙이면 spec_save(back to=spec), 패키지면 import_design(back to=import). 고친 뒤 빈 객체를 내면 다시 review 로 간다.",
                    {"back": "import|spec|api (선택)", "reason": "사유", "openapi": "바로 다시 낼 openapi 본문 (선택)"})
    p = payload if isinstance(payload, dict) else {}
    if p.get("openapi"):
        r = tools.api_submit(root, p["openapi"])
    else:
        r = _apply_fix(root, p)
    return {"last": _slim(r), "log": ["review:fix"]}


def n_build(s):
    """실행 노드 — 워크트리·상수·프롬프트를 만들고(tools.build) 이번에 띄울 역할을 state.dispatch 에 적는다.
    builder.command 가 있으면 여기서 직접 띄우고, 없으면 dispatch 노드가 클라이언트에 과제로 넘긴다."""
    root = s["root"]
    cfg = util.load_config(root)
    r = tools.build(root)
    if not r.get("ok"):
        return {"last": _slim(r), "dispatch": None, "log": ["build:blocked"]}
    plan = r["dispatch"]
    roles = plan["order"] if plan["mode"] == "parallel" else [plan["next"]] if plan["next"] else []
    if not roles:
        return {"last": _slim(r), "dispatch": None, "log": ["build:nothing"]}
    cmd = (cfg.get("builder") or {}).get("command") or ""
    if cmd:
        outs = [_run_builder(root, cfg, cmd, role, r["prompts"][role]) for role in roles]
        return {"last": {"ok": all(o["ok"] for o in outs), "builders": outs}, "dispatch": None, "log": [f"build:{','.join(roles)}"]}
    return {"last": _slim(r), "dispatch": {"roles": roles, "prompts": {ro: r["prompts"][ro] for ro in roles}},
            "log": [f"build:{','.join(roles)}"]}


def n_build_fix(s):
    root = s["root"]
    last = s.get("last") or {}
    payload = _fix(last, "build 가 거부됐다 — 본선을 정리하거나 back 으로 돌아간다.")
    return {"last": _slim(_apply_fix(root, payload)), "log": ["build:fix"]}


def n_dispatch(s):
    """묻는 노드 — 부작용 없이 interrupt 만. 재개되면 그대로 check 로 돌아가고, 리포트가 들어왔는지는 phase 가 말해 준다."""
    root = s["root"]
    d = s.get("dispatch") or {}
    roles = d.get("roles") or []
    _task("run_builder",
          f"역할 {', '.join(roles)} 을(를) 착수한다. 각 역할을 <role>-builder 서브에이전트로 띄우고 prompts 의 프롬프트를 "
          "번역·요약 없이 그대로 준다. 빌더가 precheck → report 를 마치면(report 도구) 빈 객체를 낸다. "
          "직렬이면 여기 적힌 역할 하나뿐이다 — 다음 역할은 advance 가 다시 준다.",
          {},
          roles=roles, agents={ro: f"{ro}-builder" for ro in roles},
          worktrees={ro: util.worktree(root, ro) for ro in roles}, prompts=d.get("prompts") or {})
    return {"dispatch": None, "log": [f"dispatch:{','.join(roles)}"]}


def _run_builder(root, cfg, cmd, role, prompt):
    """서버가 직접 빌더를 띄운다 (builder.command). 프롬프트는 stdin, cwd 는 워크트리."""
    wt = util.worktree(root, role)
    env = dict(os.environ, HANDOFF_ROOT=root, HANDOFF_ROLE=role, HANDOFF_WORKTREE=wt)
    try:
        p = subprocess.run(cmd, shell=True, input=prompt, cwd=wt, env=env, capture_output=True, text=True,
                           timeout=(cfg.get("builder") or {}).get("timeout_sec", 7200))
        return {"role": role, "ok": p.returncode == 0, "code": p.returncode, "tail": (p.stdout + p.stderr)[-1500:]}
    except subprocess.TimeoutExpired:
        return {"role": role, "ok": False, "code": None, "tail": "timeout"}
    except OSError as e:
        return {"role": role, "ok": False, "code": None, "tail": str(e)}


def n_verify(s):
    root = s["root"]
    r = tools.verify(root)
    if not r.get("ok"):
        p = _fix(r, "verify 가 열리지 않는다.")
        return {"last": _slim(_apply_fix(root, p)), "log": ["verify:blocked"]}
    return {"last": _slim(r), "log": [f"verify:{r.get('verdict')}"]}


def n_ship(s):
    root = s["root"]
    pre = tools.ship(root, None)
    if not pre.get("pending_human"):
        return {"last": _slim(pre), "log": ["ship:re-verify-loop"]}     # 재검사가 loop 면 phase 가 build 로 바뀌어 있다
    decision = _approval("ship", pre["approval_prompt"])
    r = tools.ship(root, approver=lambda m, i: decision)
    return {"last": _slim(r), "log": [f"ship:{'ok' if r.get('approved') else 'held'}"]}


def n_ship_fix(s):
    root = s["root"]
    last = s.get("last") or {}
    payload = _task("fix_after_hold",
                    f"완료가 보류됐다: {last.get('message')}\n더 돌리려면 back to=build (인계 자동), 계약을 고치려면 to=api|import.",
                    {"back": "build|api|spec|import", "reason": "사유"})
    return {"last": _slim(_apply_fix(root, payload if isinstance(payload, dict) else {"back": "build", "reason": "held"})),
            "log": ["ship:fix"]}


def n_done(s):
    return {"last": {"ok": True, "message": "완료. 새 패키지는 import_design, 결정 변경은 spec_save 로 새 사이클을 연다."}, "log": ["done"]}


# ─────────────────────────── 배선 ───────────────────────────

def _after_import(s):
    return "confirm" if s.get("needs_confirm") else "check"


def _after_spec(s):
    last = s.get("last") or {}
    return "check" if last.get("ok") and not last.get("draft") else "spec_ask"


def _after_review(s):
    last = s.get("last") or {}
    return "review_fix" if last.get("ok") and last.get("approved") is False else "check"


def _after_ship(s):
    last = s.get("last") or {}
    return "ship_fix" if last.get("ok") and last.get("approved") is False else "check"


def _after_build(s):
    last = s.get("last") or {}
    if last.get("ok") is False:
        return "build_fix"
    return "dispatch" if s.get("dispatch") else "check"


def build_graph():
    g = StateGraph(State)
    for name, fn in (("check", n_check), ("import", n_import), ("confirm", n_confirm), ("spec", n_spec), ("spec_ask", n_spec_ask),
                     ("api", n_api), ("review", n_review), ("review_fix", n_review_fix),
                     ("build", n_build), ("build_fix", n_build_fix), ("dispatch", n_dispatch),
                     ("verify", n_verify), ("ship", n_ship), ("ship_fix", n_ship_fix), ("done", n_done)):
        g.add_node(name, fn)
    g.add_edge(START, "check")
    g.add_conditional_edges("check", lambda s: s["phase"], {p: p for p in flow.PHASES})
    g.add_conditional_edges("import", _after_import, {"confirm": "confirm", "check": "check"})
    g.add_edge("confirm", "check")
    g.add_conditional_edges("spec", _after_spec, {"spec_ask": "spec_ask", "check": "check"})
    g.add_edge("spec_ask", "check")
    g.add_edge("api", "check")
    g.add_conditional_edges("review", _after_review, {"review_fix": "review_fix", "check": "check"})
    g.add_edge("review_fix", "check")
    g.add_conditional_edges("build", _after_build, {"build_fix": "build_fix", "dispatch": "dispatch", "check": "check"})
    g.add_edge("build_fix", "check")
    g.add_edge("dispatch", "check")
    g.add_edge("verify", "check")
    g.add_conditional_edges("ship", _after_ship, {"ship_fix": "ship_fix", "check": "check"})
    g.add_edge("ship_fix", "check")
    g.add_edge("done", END)
    return g


@contextlib.contextmanager
def _app(root):
    os.makedirs(util.ho(root), exist_ok=True)
    with SqliteSaver.from_conn_string(util.ho(root, DB)) as cp:
        yield build_graph().compile(checkpointer=cp)


def _pending(app):
    st = app.get_state(CFG)
    for t in st.tasks:
        for i in t.interrupts:
            return i.value
    return None


# ─────────────────────────── 구동 API (MCP · CLI 가 같은 것을 부른다) ───────────────────────────

def pending(root):
    """지금 멈춰 있는 지점 — {"kind": "approval"|"task", ...} 또는 None."""
    if not util.is_wired(root):
        return None
    with _app(root) as app:
        return _pending(app)


def advance(root, approver=None, resume=None):
    """그래프를 다음 멈춤 지점(또는 끝)까지 돌린다.

    승인 멈춤: approver 가 없으면 pending_human + approval_prompt 만 돌려준다 (tools.review/ship 과 같은 계약).
    과제 멈춤: resume(payload) 이 없으면 과제를 그대로 돌려준다 — submit(payload) 이 resume 을 채워 다시 부른다.
    """
    if not util.is_wired(root):
        tools.setup(root)
    with _app(root) as app:
        p = _pending(app)
        if p and p.get("kind") == "approval":
            if approver is None:
                return {"ok": False, "pending_human": True, "approval_prompt": p["prompt"], "action": p["action"],
                        "message": ("사람 승인이 필요하다. 이 클라이언트가 elicitation 을 지원하지 않으면 사람이 터미널에서 직접 실행한다:\n"
                                    f"  python3 {tools.cli_path()} run --root {root}\n\n{p['prompt']}")}
            decision = approver(p["prompt"], []) or {"approved": False, "reason": "승인 채널 없음"}
            out = app.invoke(Command(resume=decision), CFG)
        elif p and p.get("kind") == "task":
            if resume is None:
                return _task_out(root, p)
            out = app.invoke(Command(resume=resume), CFG)
        else:
            out = app.invoke({"root": root, "log": []}, CFG)
        nxt = _pending(app)
        phase = flow.current(root, util.load_config(root))["phase"]
        if nxt and nxt.get("kind") == "task":
            return _task_out(root, nxt, last=out.get("last"))
        if nxt and nxt.get("kind") == "approval":
            return {"ok": True, "phase": phase, "pending_human": True, "approval_prompt": nxt["prompt"], "action": nxt["action"],
                    "last": out.get("last"), "message": f"{flow.LABELS[phase]} — 사람 승인 대기. advance 를 다시 부르면 승인 창이 뜬다."}
        return {"ok": True, "phase": phase, "pending": None, "last": out.get("last"),
                "message": f"{flow.LABELS[phase]}. " + str((out.get("last") or {}).get("message") or "")}


def _task_out(root, p, last=None):
    phase = flow.current(root, util.load_config(root))["phase"]
    return {"ok": True, "phase": phase, "pending": p, "last": last,
            "message": (f"{flow.LABELS[phase]} — 과제 `{p['task']}`: {p['message']}\n"
                        f"submit(payload) 로 낸다. payload 모양: {p.get('submit')}")}


def submit(root, payload):
    """과제 멈춤을 payload 로 재개한다. 승인 멈춤은 재개하지 않는다 — advance(승인 채널) 만 통과한다."""
    p = pending(root)
    if not p:
        return {"ok": False, "message": "멈춰 있는 과제가 없다 — advance 를 먼저 부른다."}
    if p.get("kind") != "task":
        return {"ok": False, "message": f"지금 멈춤은 사람 승인({p.get('action')})이다 — submit 으로 재개할 수 없다. advance 를 부른다."}
    # 빈 객체("그대로 진행")도 재개 값이어야 한다 — LangGraph 는 falsy 재개 값을 "재개 없음" 으로 본다
    return advance(root, resume=payload if isinstance(payload, dict) and payload else {"ok": True})
