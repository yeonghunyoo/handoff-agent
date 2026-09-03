"""도구 — MCP 와 무관한 순수 함수. server.py(MCP) 와 run.py(CLI) 가 같은 것을 부른다.

사람 승인이 필요한 도구(review · ship)는 approver 콜백을 받는다:
  approver(message, items) -> {"approved": bool, "reason": str}
approver 가 None 이면 승인 프롬프트만 돌려준다(pending_human). 승인 도구에 approve 류 인자는 없다 —
에이전트가 인자에 넣을 수 있는 것은 게이트가 아니다.
"""
import concurrent.futures as futures
import os
import subprocess
import sys

from . import api, checks, derive, design, flow, gen, git, leaks, reports, score, util

DRAFT = "spec.draft.json"
LAST = "last-verify.json"


def _refuse(msg, **extra):
    return {"ok": False, "message": msg, **extra}


def _st(root):
    cfg = util.load_config(root)
    return cfg, flow.current(root, cfg)


def _routes(root):
    text = util.read_text(util.api_path(root))
    try:
        return api.validate(text) if text.strip() else []
    except api.ApiError:
        return []


def _render(root, cfg, st, result=None):
    """대시보드 + 계약 요약. 실패해도 흐름을 막지 않되, 실패 사실은 돌려준다."""
    try:
        rs = _routes(root)
        if result is None:
            result = util.read_json(util.ho(root, LAST))
        handoff = util.read_json(util.ho(root, util.HANDOFF))
        dash = reports.dashboard(root, cfg, st, rs, result, handoff)
        if st["manifest"]:
            reports.plan_doc(root, cfg, st, rs)
        return dash
    except Exception as e:
        return f"!! 대시보드 렌더링 실패: {type(e).__name__}: {e}"


def _save(root, st, ev, **fields):
    keep = {k: st[k] for k in util.EMPTY_STATE if k in st}
    util.record(root, keep, ev, **fields)


def cli_path():
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "run.py"))


def _pending(root, action, prompt):
    return {"ok": False, "pending_human": True, "approval_prompt": prompt,
            "message": ("사람 승인이 필요하다. 이 클라이언트가 elicitation 을 지원하지 않으면 사람이 터미널에서 "
                        f"직접 실행한다:\n  python3 {cli_path()} {action} --root {root}\n\n{prompt}")}


# ─────────────────────────── 조회 · 배선 ───────────────────────────

def status(root):
    if not util.is_wired(root):
        cands = design.find_candidates(root)
        return {"ok": False, "wired": False, "candidates": cands,
                "message": "이 레포는 아직 배선되지 않았다. 여기서 쓰려면 setup 을 부른다 (config · .gitignore · CLAUDE.md 절)."
                           + _cand_note(cands)}
    cfg, st = _st(root)
    steps = [{"phase": p, "label": flow.LABELS[p], "current": p == st["phase"],
              "passed": flow.idx(p) < flow.idx(st["phase"])} for p in flow.PHASES]
    if st["phase"] == "spec":
        draft = util.read_json(util.ho(root, DRAFT))
        if draft:
            st["warnings"].append("인터뷰 진행 중 — 남은 항목: " + "; ".join(flow.spec_problems(draft)))
        hints = design.readme_hints(root, st["manifest"])
        if hints:
            st["hints"] = hints
    out = {"ok": True, "wired": True, "phase": st["phase"], "label": st["label"], "version": st["version"],
           "cycle": st["cycle"], "human": st["human"], "next": st["next"], "warnings": st["warnings"],
           "fingerprint": st["fingerprint"], "roles": st.get("roles"), "reports": st.get("reports"),
           "steps": steps, "hints": st.get("hints"),
           "message": f"{st['label']} · 계약 v{st['version']} · 지문 [{st['fingerprint']}]\n다음: {st['next']}"}
    if st["manifest"]:
        m = st["manifest"]
        out["design"] = {"screens": [s["id"] for s in m["screens"]], "tokens": len(m["tokens"]), "docs": m["docs"]}
    if st["phase"] == "import":
        out["candidates"] = design.find_candidates(root)
        out["message"] += _cand_note(out["candidates"])
    return out


def _cand_note(cands):
    """status 메시지 꼬리 — 레포 루트에서 찾은 패키지 후보. 사용자가 경로를 말하지 않아도 되게 한다."""
    if not cands:
        return ""
    rows = "\n  ".join(f'{c["path"]}  ({c["why"]})' for c in cands)
    if len(cands) == 1:
        return (f"\n레포 루트에서 패키지 후보를 찾았다:\n  {rows}\n"
                f"사용자에게 이것으로 등록할지 확인한 뒤 import_design(path=\"{cands[0]['path'].rstrip('/')}\") 를 부른다.")
    return f"\n레포 루트에서 패키지 후보를 찾았다 — 사용자에게 고르게 한다:\n  {rows}"


def setup(root):
    """레포 배선 — config · .gitignore · CLAUDE.md 절. 멱등."""
    if not git.is_repo(root):
        git.run(root, "init", "-b", "main")
    if not util.is_wired(root):
        util.write_config(root)
    ig = os.path.join(root, ".gitignore")
    cur = util.read_text(ig)
    block = ("\n# handoff (도구 상태 · 민감 파일은 커밋하지 않는다 — 예시는 .example 로)\n"
             ".handoff/\ndocs/handoff-screens/\n.env\n.env.*\n!.env.example\n*.pem\n*.p8\n*.p12\n*.keystore\n*.jks\n"
             "*.tfstate\n*.tfvars\n*credentials*.json\n*service-account*.json\n")
    if "# handoff (" not in cur:
        util.write_text(ig, cur.rstrip("\n") + block)
    md = os.path.join(root, "CLAUDE.md")
    cur = util.read_text(md)
    if "## handoff 워크플로" not in cur:
        util.write_text(md, cur.rstrip("\n") + "\n\n" + CLAUDE_SECTION)
    name = git.run(root, "config", "user.name", check=False).stdout.strip()
    warn = "" if name else "\n! git user.name / user.email 이 없다 — 계약 확정·머지가 커밋을 남기므로 설정해야 한다."
    return {"ok": True, "message": "배선 끝 — .handoff/config.json · .gitignore · CLAUDE.md 절." + warn
            + "\n" + status(root)["message"]}


CLAUDE_SECTION = """## handoff 워크플로

이 레포는 Claude Design 핸드오프 패키지로 iOS · Android · backend 를 한 번에 만든다. **개발 작업 전에 반드시
MCP 도구 `status` 를 부르고 `next` 를 따른다.** 절차는 `/handoff` 스킬에 있다.

- 순서: 패키지 등록 → 스펙·인프라 결정 → openapi → 계약 확정(사람) → 병렬 구현 → 검사 → 완료 승인(사람)
- `design/` · `api/` · `shared/generated/` 는 직접 편집하지 않는다. 계약 변경은 `back` 으로 되돌아가 재승인.
- 구현은 `.handoff/worktrees/<역할>` 안에서만 한다. 승인은 사람만 한다 (elicitation).
"""


# ─────────────────────────── ① 패키지 ───────────────────────────

def import_design(root, path=None, screens=None, components=None):
    cfg, st = _st(root)
    if flow.idx(st["phase"]) > flow.idx("review") and st["phase"] != "done":
        return _refuse(flow.require(st, "import", "spec", "api", "review", "done"))
    try:
        src = design.import_package(root, path) if path else os.path.basename(util.design_dir(root))
        if screens or components:
            design.confirm_screens(root, screens, components)
        m = design.scan(root)
        dv = derive.write_all(root, m)          # 파생물(문구·아이콘·모델·전이·컴포넌트·내비·의도·규칙) — 지문에 든다
        detail = dv.pop("_detail", None) or {}
        if m.get("confirmed"):
            design.write_manifest(root, m, detail)   # 확정 매니페스트에 상세를 채운다
        m = design.scan(root)
    except design.DesignError as e:
        return _refuse(f"패키지를 못 읽었다: {e}")
    comps = m["components"] if m.get("confirmed") else (detail.get("components") or [])
    overlays = [c for c in comps if c["type"] in ("sheet", "modal", "popover")]
    if st["phase"] == "done":
        st["cycle"] += 1
    spec_ok = not flow.spec_problems(util.read_spec(root))
    st["phase"] = "api" if spec_ok else "spec"
    st["roles"], st["reports"] = [], []
    _save(root, st, "design_imported", source=src, hash=m["hash"][:12],
          screens=[s["id"] for s in m["screens"]], tokens=len(m["tokens"]))
    cfg, st = _st(root)
    warn = "" if m["tokens"] else "\n! 토큰이 없다 — 색·치수 하드코딩 검출이 비활성이다 (패키지에 tokens.json 이나 css 변수가 있으면 잡힌다)."
    if m.get("boards"):
        warn += "\n탐색 보드(여러 안 비교 — 화면 아님, 참고 자료): " + ", ".join(m["boards"])
    if any(s.get("state") for s in m["screens"]) and not m.get("confirmed"):
        warn += ("\n! 프로토타입 한 파일 안의 상태 분기를 화면 후보로 뽑았다 — **사용자에게 목록을 보이고 확정받은 뒤** "
                 "import_design(screens=[{id,title,file,anchor}...]) 로 다시 부른다 (design/handoff.manifest.json 에 남는다).")
    if overlays and not m.get("components_confirmed"):
        warn += ("\n! 오버레이 " + ", ".join(f"{c['id']}({c['type']})" for c in overlays)
                 + " 는 화면이 아니라 **컴포넌트**로 잡았다 — 사용자에게 보이고, 이름·타입을 고치거나 화면으로 승격하려면 "
                   "import_design(components=[{id,type,title,anchor}...]) 또는 screens=… 로 다시 부른다. 그대로면 확정 없이도 매니페스트에 든다.")
    hints = design.readme_hints(root, m)
    ctypes = " · ".join(f"{t} {n}" for t, n in sorted(dv["components"].items())) or "없음"
    return {"ok": True, "source": src, "confirmed": m.get("confirmed", False), "derived": dv,
            "screens": [{"id": s["id"], "title": s["title"], "file": s["file"], "anchor": s.get("anchor")} for s in m["screens"]],
            "components": [{k: c.get(k) for k in ("id", "type", "title", "screen", "anchor", "handler") if c.get(k) is not None}
                           for c in comps],
            "navigation": {k: v for k, v in (detail.get("navigation") or {}).items() if k != "transitions"},
            "boards": m.get("boards", []), "tokens": len(m["tokens"]), "docs": m["docs"], "chats": m.get("chats", []),
            "assets": len(m["assets"]), "hints": hints,
            "dashboard": _render(root, cfg, st),
            "message": (f"패키지 등록: 화면 {len(m['screens'])}개 · 컴포넌트 {len(comps)}개 ({ctypes}) · 토큰 {len(m['tokens'])}개 · "
                        f"문서 {len(m['docs'])}개 · 파생: 문구 {dv['strings']} · 아이콘 {dv['icons']} · "
                        f"모델 {', '.join(dv['entities']) or '없음'} · 핸들러 {dv['handlers']} · "
                        f"전이 {dv['navigation']['transitions']} · 의도 {dv['intent_turns']}턴 (design/, 지문 {st['fingerprint']}).{warn}\n"
                        "화면 목록과 컴포넌트(타입) 목록을 사용자에게 보이고 빠진 것이 없는지 확인받는다.\n"
                        + (f"README 의 스택 힌트 (기본값 제안 — 사람이 확정):\n  " + "\n  ".join(hints) + "\n" if hints else "")
                        + f"다음: {st['next']}")}


# ─────────────────────────── ② 스펙 ───────────────────────────

def spec_save(root, spec):
    cfg, st = _st(root)
    why = flow.require(st, "spec", "api", "review", "done")
    if why:
        return _refuse(why)
    base = util.read_spec(root) if st["phase"] != "spec" else (util.read_json(util.ho(root, DRAFT)) or {})
    merged = {**(base or {}), **(spec if isinstance(spec, dict) else {})}
    problems = flow.spec_problems(merged)
    if problems:
        util.write_json(util.ho(root, DRAFT), merged)
        return {"ok": True, "draft": True, "remaining": problems,
                "message": "임시 저장 — 남은 항목만 이어서 묻는다:\n" + "\n".join(f"  · {p}" for p in problems)}
    merged = leaks.mask_deep(merged)
    merged["platforms"] = [p for p in util.APPS if p in [str(x).lower() for x in merged["platforms"]]]
    util.write_json(util.ho(root, util.SPEC), merged)
    try:
        os.remove(util.ho(root, DRAFT))
    except OSError:
        pass
    if st["phase"] == "done":
        st["cycle"] += 1
        st["phase"] = "api"
    elif st["phase"] == "spec":
        st["phase"] = "api"
    _save(root, st, "spec_saved", platforms=merged["platforms"], stack=merged.get("stack"), infra=merged.get("infra"))
    cfg, st = _st(root)
    return {"ok": True, "spec": merged, "message": f"스펙 확정: {merged['platforms']} · {merged.get('stack')} · {merged.get('infra')}\n다음: {st['next']}"}


# ─────────────────────────── ③ API ───────────────────────────

def api_submit(root, openapi):
    cfg, st = _st(root)
    why = flow.require(st, "api", "review")
    if why:
        return _refuse(why)
    if not isinstance(openapi, str) or not openapi.strip():
        return _refuse("openapi 는 openapi.yaml 의 본문(문자열)이다.")
    if leaks.find(openapi):
        return _refuse("openapi 에 시크릿으로 보이는 값이 있다 — 자리표시(YOUR_API_KEY)만 넣는다. 쓰지 않았다.")
    try:
        rs = api.validate(openapi)
    except api.ApiError as e:
        return _refuse(f"openapi 검증 실패 — 쓰지 않았다: {e}")
    util.write_text(util.api_path(root), openapi if openapi.endswith("\n") else openapi + "\n")
    st["phase"] = "review"
    _save(root, st, "api_submitted", routes=len(rs))
    cfg, st = _st(root)
    dash = _render(root, cfg, st)
    return {"ok": True, "routes": rs, "dashboard": dash,
            "message": (f"api/openapi.yaml 저장: 라우트 {len(rs)}개 (지문 {st['fingerprint']}).\n"
                        f"대시보드를 아티팩트로 발행해 사용자에게 보이고 review 를 부른다: {dash}\n"
                        f"다음: {st['next']}")}


# ─────────────────────────── ④ 계약 확정 (사람) ───────────────────────────

def review_prompt(root, st):
    m = st["manifest"] or {}
    rs = _routes(root)
    spec = util.read_spec(root) or {}
    return (f"계약 확정 v{st['version'] + 1} — 지문 [{st['fingerprint']}]\n"
            f"대시보드 헤더의 지문과 같은지 확인하십시오. 다르면 방금 본 것과 다른 계약입니다.\n"
            f"  · 화면 {len(m.get('screens', []))}개 · 토큰 {len(m.get('tokens', {}))}개 · 라우트 {len(rs)}개\n"
            f"  · 플랫폼 {', '.join(spec.get('platforms') or [])} · 스택 {spec.get('stack')} · 인프라 {spec.get('infra')}\n"
            "승인하면 design/ 와 api/ 가 본선에 커밋·잠금되고 구현이 시작됩니다. 반려 사유는 기록됩니다.")


def review(root, approver=None):
    cfg, st = _st(root)
    why = flow.require(st, "review")
    if why:
        return _refuse(why)
    blockers = []
    if not git.is_repo(root):
        blockers.append("git 저장소가 아니다 — setup 이 git init 을 한다")
    probs = flow.spec_problems(util.read_spec(root))
    if probs:
        blockers.append("스펙 불완전: " + "; ".join(probs))
    if not _routes(root):
        blockers.append("api/openapi.yaml 이 없거나 라우트가 없다")
    if blockers:
        return _refuse("확정 불가:\n" + "\n".join(f"  × {b}" for b in blockers))
    prompt = review_prompt(root, st)
    if approver is None:
        return _pending(root, "review", prompt)
    decision = approver(prompt, []) or {"approved": False, "reason": "승인 채널 없음"}
    if not decision.get("approved"):
        reason = decision.get("reason") or "(사유 없음)"
        _save(root, st, "review_rejected", reason=reason)
        return {"ok": True, "approved": False,
                "message": (f"반려 — {reason}. 사유에 맞게 고친다: openapi 면 api_submit 으로 다시 내고, 스펙이면 "
                            "spec_save, 패키지면 import_design (또는 back). 고친 뒤 review 를 다시 부른다.")}
    version = st["version"] + 1
    m = st["manifest"]
    try:
        gen.expected(root, cfg, version, m, _routes(root))
    except Exception as e:
        return _refuse(f"생성 상수를 만들 수 없다 — 계약을 고쳐야 한다: {type(e).__name__}: {e}")
    st["version"] = version
    st["locked"] = {"version": version, "hash": st["fingerprint"]}
    st["phase"] = "build"
    st["roles"], st["reports"], st["verdict"] = [], [], None
    reports.plan_doc(root, cfg, dict(st, version=version), _routes(root))
    if not git.has_commits(root):
        git.run(root, "commit", "--allow-empty", "-q", "-m", "handoff: init")
    git.commit_paths(root, f"handoff: 계약 v{version} 확정 (지문 {st['fingerprint']})",
                     util.DESIGN_DIR, util.API_DIR, util.DOCS_DIR, ".gitignore", "CLAUDE.md")
    _save(root, st, "locked", version=version, hash=st["fingerprint"])
    cfg, st = _st(root)
    _render(root, cfg, st)
    return {"ok": True, "approved": True, "version": version,
            "message": f"계약 v{version} 확정 · 본선 커밋. {st['label']} — {st['next']}"}


# ─────────────────────────── 되돌리기 ───────────────────────────

BACK = ("import", "spec", "api", "build")


def back(root, to, reason):
    cfg, st = _st(root)
    if to not in BACK:
        return _refuse(f"to 는 {', '.join(BACK)} 중 하나다.")
    if not reason:
        return _refuse("reason 이 필요하다 — 왜 돌아가는지 기록에 남는다.")
    if flow.idx(to) >= flow.idx(st["phase"]) and st["phase"] != "done":
        return _refuse(f"지금 단계({st['label']})보다 앞으로는 못 간다.")
    st["phase"] = to
    if flow.idx(to) <= flow.idx("build"):
        st["roles"], st["reports"] = [], []
    _save(root, st, "back", to=to, reason=reason)
    cfg, st = _st(root)
    note = ("" if to == "build" else
            "\n주의: design/ 나 api/ 를 바꾸면 지문이 달라져 재승인(review)을 거친다. 워크트리는 남아 있다 — "
            "재승인 뒤 build 가 새 상수를 다시 놓는다.")
    return {"ok": True, "message": f"{flow.LABELS[to]} 로 복귀 ({reason}).{note}\n다음: {st['next']}"}


# ─────────────────────────── ⑤ 구현 ───────────────────────────

def build(root):
    cfg, st = _st(root)
    why = flow.require(st, "build")
    if why:
        return _refuse(why)
    roles = util.active_roles(root)
    if st.get("roles"):
        pending = [r for r in st["roles"] if r not in st["reports"]]
        info = {}
        for r in pending:
            wt = util.worktree(root, r)
            info[r] = ("(워크트리 없음)" if not os.path.isdir(wt)
                       else git.run(wt, "status", "--porcelain", check=False).stdout.strip()[:1500])
        return {"ok": True, "resume": True, "pending": pending, "worktrees": info,
                "message": f"이미 착수된 루프다 — 재착수가 아니라 이어간다. 리포트 미제출: {', '.join(pending) or '없음'}"}
    dirty = git.dirty_main(root)
    if dirty:
        return _refuse("본선 작업트리에 미커밋 변경이 있다 — 워크트리가 낡은 계약을 물려받는다: " + ", ".join(dirty[:5]))
    t = checks.targets(root)
    files = gen.expected(root, cfg, st["version"], st["manifest"], t["routes"])
    handoff = util.read_json(util.ho(root, util.HANDOFF))
    if handoff and handoff.get("version") != st["version"]:
        handoff = None                                    # 다른 계약의 인계는 낡았다
    trees, errs = {}, {}
    with futures.ThreadPoolExecutor(max_workers=len(roles)) as ex:
        fs = {ex.submit(_prepare_worktree, root, r, files, st["version"]): r for r in roles}
        for f in futures.as_completed(fs):
            r = fs[f]
            try:
                trees[r] = f.result()
            except Exception as e:
                errs[r] = f"{type(e).__name__}: {e}"
    if errs:
        return _refuse("워크트리 준비 실패 — 착수하지 않았다:\n" + "\n".join(f"  × {r}: {e}" for r, e in errs.items()))
    prompts = {r: reports.kickoff(root, cfg, r, st, t, handoff) for r in roles}
    st["roles"], st["reports"] = roles, []
    _save(root, st, "dispatched", roles=roles, version=st["version"])
    cfg, st = _st(root)
    _render(root, cfg, st)
    return {"ok": True, "roles": roles, "worktrees": trees, "prompts": prompts,
            "message": (f"워크트리 {len(roles)}개 준비 (계약 v{st['version']}, 생성 상수 {len(files)}개). "
                        f"각 역할을 에이전트({', '.join(r + '-builder' for r in roles)})로 **병렬** 착수하되 prompts 의 "
                        "해당 프롬프트를 번역·요약 없이 그대로 전달한다. 진행 중에는 관여하지 않는다.")}


def _prepare_worktree(root, role, files, version):
    wt, created = git.ensure_worktree(root, role)
    gen.write(wt, files)
    git.commit_paths(wt, f"handoff: 생성 상수 v{version} (서버 — 손대면 검사가 잡는다)", util.GEN_DIR)
    return {"path": wt, "branch": git.branch(role), "created": created}


def precheck(root, role):
    cfg, st = _st(root)
    if role not in util.ROLES:
        return _refuse(f"Bad `role`. One of: {'/'.join(util.ROLES)}.")
    t = checks.targets(root)
    e = score.evaluate_role(root, cfg, role, t, st["version"], precheck=True)
    cons = e["consumption"]
    ok = not e["blockers"]
    return {"ok": True, "passed": ok, "blockers": e["blockers"],
            "consumption": {"used": cons["used"], "total": cons["total"], "rate": cons["rate"],
                            "unused": [i["id"] + " " + (i["const"] or i["label"]) for i in cons["items"] if not i["used"]]},
            "hardcodes": e["hardcodes"][:30], "test_bypass": e["bypass"],
            "message": (("PRECHECK PASS. Next: report(role, report) [R1]." if ok else
                         "PRECHECK FAIL. Fix every blocker, rerun precheck [R1]. A report with an open blocker is refused "
                         "unless status=blocked.")
                        + f" consumption {cons['rate']}% · hardcodes {len(e['hardcodes'])} [C3] · "
                          f"skips {len(e['bypass']['skips'])} · deleted tests {len(e['bypass']['deleted_tests'])} [T2].")}


def report(root, role, rep):
    cfg, st = _st(root)
    why = flow.require(st, "build")
    if why:
        return _refuse(why)
    if role not in st.get("roles", []):
        return _refuse(f"`{role}` was not dispatched this loop (dispatched: {', '.join(st.get('roles') or [])}).")
    problems = score.validate_report(rep)
    if problems:
        return _refuse("REJECTED [R2]: report shape.\n" + "\n".join(f"  x {p}" for p in problems))
    wt = util.worktree(root, role)
    if os.path.isdir(wt):
        git.commit_all(wt, f"handoff({role}): 리포트 제출 시점 마감 v{st['version']}")
    if rep.get("status") != "blocked":
        e = score.evaluate_role(root, cfg, role, checks.targets(root), st["version"], precheck=True)
        if e["blockers"]:
            return _refuse("REJECTED [R1]: open blockers. Not recorded. Fix and resubmit, or submit status=blocked "
                           "with tried + error [R2].\n" + "\n".join(f"  x {b}" for b in e["blockers"]))
    rec = leaks.mask_deep({**rep, "_role": role, "_version": st["version"], "_at": util.now()})
    util.write_json(util.ho(root, util.REPORTS, f"{role}.json"), rec)
    if role not in st["reports"]:
        st["reports"].append(role)
    remaining = [r for r in st["roles"] if r not in st["reports"]]
    if not remaining:
        st["phase"] = "verify"
    _save(root, st, "reported", role=role, status=rep.get("status"))
    out = {"ok": True, "accepted": True, "message": "ACCEPTED. " + (f"Waiting on: {', '.join(remaining)}." if remaining else "All reports in.")}
    if not remaining:
        auto = verify(root)
        out["verify"] = auto
        out["message"] += " Verify ran automatically.\n" + auto.get("message", "")
    return out


# ─────────────────────────── ⑥ 검사 ───────────────────────────

def verify(root):
    cfg, st = _st(root)
    why = flow.require(st, "verify")
    if why:
        return _refuse(why)
    roles = st.get("roles") or util.active_roles(root)
    for r in roles:
        wt = util.worktree(root, r)
        if os.path.isdir(wt):
            git.commit_all(wt, f"handoff({r}): 검사 마감 자동 커밋 v{st['version']}")
    result = score.evaluate(root, cfg, roles, st["version"])
    util.write_json(util.ho(root, LAST), result)
    doc = reports.verify_doc(root, result, st["version"])
    page = reports.screens_page(root, st, roles)
    if result["verdict"] == "pass":
        st["phase"] = "ship"
    else:
        score.write_handoff(root, result, st["version"])
        st["phase"] = "build"
        st["roles"], st["reports"] = [], []
    st["verdict"] = result["verdict"]
    _save(root, st, "verified", score=result["score"], verdict=result["verdict"], blockers=len(result["blockers"]))
    cfg, st = _st(root)
    dash = _render(root, cfg, st, result)
    proposals = [p for e in result["roles"].values() for p in ((e.get("report") or {}).get("proposals") or [])]
    if result["verdict"] == "pass":
        nxt = "⑦ ship — 사람 승인 뒤 본선 머지."
    elif proposals:
        nxt = ("계약 수정 제안이 있다 — 사람에게 보이고, 수용하면 back(to='api' 또는 'import') 로 계약을 고친다. "
               "아니면 build 로 재착수한다 (인계 자동 포함).")
    else:
        nxt = "build 로 재착수한다 (인계 자동 포함)."
    return {"ok": True, "verdict": result["verdict"], "score": result["score"], "threshold": result["threshold"],
            "blockers": result["blockers"], "components": result["components"], "parity": result["parity"],
            "proposals": proposals, "doc": doc, "screens_page": page, "dashboard": dash,
            "message": (f"점수 {result['score']}/{result['threshold']} → {result['verdict']}. "
                        f"대시보드를 아티팩트로 발행해 사용자에게 보인다: {dash}\n"
                        + (f"화면 대조(사진, 레포에서 연다): {page}\n" if page else "")
                        + f"다음: {nxt}")}


# ─────────────────────────── ⑦ 완료 승인 (사람) ───────────────────────────

def ship(root, approver=None):
    cfg, st = _st(root)
    why = flow.require(st, "ship")
    if why:
        return _refuse(why)
    roles = st.get("roles") or util.active_roles(root)
    result = score.evaluate(root, cfg, roles, st["version"])         # 저장값을 믿지 않는다 — 다시 잰다
    util.write_json(util.ho(root, LAST), result)
    if result["verdict"] != "pass":
        score.write_handoff(root, result, st["version"])
        st["phase"], st["roles"], st["reports"] = "build", [], []
        _save(root, st, "verified", score=result["score"], verdict="loop", blockers=len(result["blockers"]))
        return _refuse(f"재검사 결과 pass 가 아니다 (점수 {result['score']}) — build 로 돌아간다.\n"
                       + "\n".join(f"  × {b}" for b in result["blockers"]))
    items = score.exceptions(result)
    prompt = (f"완료 승인 — 계약 v{st['version']} [지문 {st['fingerprint']}] · 점수 {result['score']}. "
              "승인하면 구현 브랜치를 본선에 머지합니다.")
    if items:
        prompt += "\n함께 승인되는 예외 항목:\n" + "\n".join(f"  · {i}" for i in items[:25])
    page = os.path.join(root, util.DOCS_DIR, reports.SCREENS_DIR, "index.html")
    if os.path.isfile(page):
        prompt += f"\n화면 대조: {util.DOCS_DIR}/{reports.SCREENS_DIR}/index.html — 레포에서 열어 눈으로 확인하십시오."
    if approver is None:
        return _pending(root, "ship", prompt)
    decision = approver(prompt, items) or {"approved": False, "reason": "승인 채널 없음"}
    if not decision.get("approved"):
        reason = decision.get("reason") or "(사유 없음)"
        _save(root, st, "ship_held", reason=reason)
        return {"ok": True, "approved": False, "message": f"보류 — {reason}. 더 돌리려면 back(to='build', reason=...)."}
    dirty = git.dirty_main(root)
    if dirty:
        return _refuse("본선 작업트리가 깨끗하지 않아 머지할 수 없다: " + ", ".join(dirty[:10]))
    git.commit_paths(root, f"handoff: 검사 리포트 v{st['version']}", util.DOCS_DIR)
    merges = {}
    for r in roles:
        b = git.branch(r)
        if git.branch_exists(root, b) and git.changed_files(root, b):
            try:
                merges[r] = git.merge(root, b, f"handoff: {r} 계약 v{st['version']} 구현 머지 (점수 {result['score']})")
            except git.GitError as e:
                return _refuse(f"머지 실패 — 수동 해소가 필요하다: {e}")
    git.remove_worktrees(root, roles)
    st["phase"] = "done"
    _save(root, st, "shipped", merges=merges, exceptions=items, version=st["version"])
    cfg, st = _st(root)
    _render(root, cfg, st, result)
    return {"ok": True, "approved": True, "merges": merges,
            "message": f"완료 — {len(merges)}개 브랜치를 본선에 머지했다. 새 요구는 import_design 또는 spec_save 로 새 사이클을 연다."}


# 도구 호출 하나 = git 읽기 캐시 수명
def _scoped(fn):
    def inner(*a, **kw):
        with git.cached():
            return fn(*a, **kw)
    inner.__name__, inner.__doc__ = fn.__name__, fn.__doc__
    return inner


for _n in ("status", "setup", "import_design", "spec_save", "api_submit", "review", "back", "build",
           "precheck", "report", "verify", "ship"):
    globals()[_n] = _scoped(globals()[_n])
