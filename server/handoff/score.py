"""판정 — 가중 점수 + 임계치 + 하드 블로커.

  점수 = w·소비 + w·테스트 + w·파리티      (잴 수 없는 성분은 빼고 정규화)
  소비   = 역할별 계약 소비율 − 하드코딩 감점
  테스트 = 서버 재실행 결과 > 자기신고. 계약 항목을 하나도 안 건드린 스위트는 증거가 아니라 제외
  파리티 = 100 − 벌점 × (미승인 발산 + 실측 갭)
  블로커가 하나라도 있으면 점수와 무관하게 loop.

착수 프롬프트의 체크리스트 · precheck · verify 가 전부 같은 함수를 쓴다 — 몰라서 틀리는 루프를 없앤다.
"""
import os

from . import checks, git, leaks, util

REPORT_STATUS = ("done", "partial", "blocked")


def read_report(root, role):
    return util.read_json(util.ho(root, util.REPORTS, f"{role}.json"))


def validate_report(rep):
    """접수 조건. 문제 목록 (빈 목록이면 통과)."""
    p = []
    if not isinstance(rep, dict):
        return ["report must be an object"]
    if rep.get("status") not in REPORT_STATUS:
        p.append(f"status must be one of {'/'.join(REPORT_STATUS)}")
    for k in ("not_done", "blocked", "divergences", "proposals", "human_check"):
        if k in rep and not isinstance(rep[k], list):
            p.append(f"{k} must be an array")
    if rep.get("status") == "blocked":
        bl = rep.get("blocked") or []
        if not bl:
            p.append("status=blocked requires blocked[] entries")
        for b in bl:
            if not isinstance(b, dict) or not b.get("tried") or not b.get("error"):
                p.append("each blocked entry needs `tried` (list of attempts) and `error` (verbatim)")
                break
    for k in ("build", "tests"):
        if k in rep and not isinstance(rep[k], dict):
            p.append(f"{k} must be an object")
    return p


def evaluate_role(root, cfg, role, t, version, precheck=False):
    role_path = cfg["roles"].get(role, role)
    wt = util.worktree(root, role)
    tree = wt if os.path.isdir(wt) else root
    report = None if precheck else read_report(root, role)
    spec = util.read_spec(root) or {}
    approved = [str(x) for x in (spec.get("divergences") or [])]

    files = checks.changed(root, role)
    vio = checks.violations(root, cfg, role, files)
    drift = checks.skeleton_drift(root, cfg, role, version)
    cons = checks.consumption(tree, role_path, cfg, role, t)
    hard = checks.hardcodes(tree, role_path, cfg, role, t)
    tokens = checks.token_usage(tree, role_path, cfg, t, role=role) if role != "backend" else []
    bypass = checks.test_bypass(root, cfg, role)
    prov = checks.test_provenance(root, cfg, role)
    cov = checks.test_coverage(tree, role_path, cfg, role, t)
    verify = checks.run_verify(root, cfg, role)

    blockers = []
    if vio["protected"]:
        blockers.append("[C1] protected paths changed in the worktree (design/ api/ .handoff/) — "
                        "propose changes via report.proposals instead: " + ", ".join(vio["protected"][:5]))
    if vio["trespass"]:
        blockers.append(f"[W1] wrote outside your role path ({role_path}/): " + ", ".join(vio["trespass"][:5]))
    if drift:
        blockers.append("[C2] shared/generated/ differs from the server's output: "
                        + ", ".join(f"{why} {p}" for why, p in drift[:5]))
    hits = checks.secret_hits(tree, role_path, cfg, role=role)
    if hits:
        blockers.append("[S1] secret-looking values in code — move to .env, commit only .env.example: "
                        + ", ".join(hits[:5]))
    sens = [f for f in files if leaks.is_sensitive_file(f)]
    if sens:
        blockers.append("[S2] sensitive files on the branch — delete, keep only *.example: " + ", ".join(sens[:5]))
    hist = checks.history_leaks(root, role)
    if hist:
        blockers.append("[S2] secrets in commit history — removing from the file is not enough; "
                        "rewrite the branch without them: " + ", ".join(hist[:5]))
    dirty = git.dirty_main(root)
    if dirty:
        blockers.append("[W2] main working tree is dirty — something wrote outside the worktrees: "
                        + ", ".join(dirty[:5]))
    if verify and verify.get("ran") and verify["passed"] < verify["ran"]:
        blockers.append(f"[T1] verify commands failed {verify['ran'] - verify['passed']}/{verify['ran']}")
    if not precheck:
        if report is None:
            blockers.append("[R1] no report — submit one with report()")
        elif report.get("status") == "blocked":
            blockers.append("[R3] blocked report — needs a human decision")
        if report and isinstance(report.get("build"), dict) and report["build"].get("ok") is False:
            blockers.append("[T1] build failed (self-reported)")
        rv = (report or {}).get("_version")
        if report is not None and rv is not None and rv != version:
            blockers.append(f"[R1] report is for contract v{rv}, current is v{version}")

    if verify and verify.get("ran"):
        tests_score, src = verify["passed"] / verify["ran"] * 100, "server"
    else:
        tr = (report or {}).get("tests") or {}
        p, f = tr.get("passed"), tr.get("failed")
        if isinstance(p, int) and isinstance(f, int) and p + f > 0:
            tests_score, src = p / (p + f) * 100, "self-reported"
        else:
            tests_score, src = None, "none"
    if cov["total"] and cov["used"] == 0 and tests_score is not None:
        tests_score, src = None, "uncontracted"        # 계약을 안 건드리는 스위트는 증거가 아니다

    reported = [d for d in ((report or {}).get("divergences") or []) if isinstance(d, (dict, str))]
    unapproved = [d for d in reported
                  if not checks._covered((d.get("topic") if isinstance(d, dict) else d), approved)]

    return {"role": role, "role_path": role_path, "blockers": blockers,
            "consumption": cons, "hardcodes": hard, "tokens": tokens, "bypass": bypass,
            "test_provenance": prov, "test_coverage": cov,
            "tests": {"score": tests_score, "source": src, "detail": verify},
            "unapproved_divergences": unapproved, "report": report, "changed": files}


def evaluate(root, cfg, roles, version):
    sc = cfg["score"]
    t = checks.targets(root)
    per = {r: evaluate_role(root, cfg, r, t, version) for r in roles}
    spec = util.read_spec(root) or {}
    approved = [str(x) for x in (spec.get("divergences") or [])]

    cons = [max(0.0, per[r]["consumption"]["rate"] - sc["hardcode_penalty"] * len(per[r]["hardcodes"]))
            for r in roles]
    cons_score = sum(cons) / len(cons) if cons else 100.0
    ts = [per[r]["tests"]["score"] for r in roles if per[r]["tests"]["score"] is not None]
    tests_score = sum(ts) / len(ts) if ts else None
    mobile = [r for r in roles if r in util.MOBILE]
    gaps = checks.parity(per[mobile[0]], per[mobile[1]], approved) if len(mobile) == 2 else []   # iOS ↔ Android — 그대로
    web_gaps = (checks.parity_web(per["web"], [per[r] for r in mobile], approved, t.get("gesture_handlers"))
                if "web" in roles and mobile else [])                                          # web ↔ 모바일 합집합 — web 이 있을 때만
    unapproved = sum(len(per[r]["unapproved_divergences"]) for r in roles)
    parity_score = max(0.0, 100.0 - sc["divergence_penalty"] * (unapproved + len(gaps) + len(web_gaps)))

    w = dict(sc["weights"])
    if tests_score is None:
        w.pop("tests", None)
    total_w = sum(w.values()) or 1.0
    score = (w.get("consumption", 0) * cons_score + w.get("tests", 0) * (tests_score or 0)
             + w.get("parity", 0) * parity_score) / total_w

    blockers, seen = [], set()
    for r in roles:
        for b in per[r]["blockers"]:
            key = b if b.startswith("[W2]") else f"[{r}] {b}"
            if key not in seen:
                seen.add(key)
                blockers.append(key)
    verdict = "pass" if not blockers and score >= sc["threshold"] else "loop"
    return {"roles": per, "score": round(score, 1), "threshold": sc["threshold"], "verdict": verdict,
            "blockers": blockers, "parity": gaps, "parity_web": web_gaps,
            "components": {"consumption": round(cons_score, 1),
                           "tests": round(tests_score, 1) if tests_score is not None else None,
                           "parity": round(parity_score, 1),
                           "hardcodes": sum(len(per[r]["hardcodes"]) for r in roles),
                           "unapproved_divergences": unapproved, "parity_gaps": len(gaps) + len(web_gaps),
                           "tests_source": {r: per[r]["tests"]["source"] for r in roles}}}


def exceptions(result):
    """ship 승인 때 함께 승인되는 예외 항목 — 사람이 보고 승인하는 것."""
    items = []
    for r, e in result["roles"].items():
        for s in e["bypass"]["skips"]:
            items.append(f"[{r}] test skipped: {s['file']} — {s['text'][:80]}")
        for d in e["bypass"]["deleted_tests"]:
            items.append(f"[{r}] test deleted: {d}")
        for h in e["hardcodes"][:10]:
            items.append(f"[{r}] hardcoded {h['kind']} at {h['file']}:{h['line']}"
                         + (f" (use {h['token']})" if h.get("token") else ""))
        for d in e["unapproved_divergences"]:
            items.append(f"[{r}] unapproved divergence: {(d.get('topic') if isinstance(d, dict) else d)}")
        for x in ((e.get("report") or {}).get("human_check") or [])[:10]:
            items.append(f"[{r}] human check: {str(x)[:160]}")
    for g in result["parity"]:
        items.append(f"[{g['missing']}] parity gap {g['kind']} {g['id']} — only {g['done']} did it")
    for g in result.get("parity_web") or []:
        items.append(f"[{g['missing']}] parity gap (web↔mobile) {g['kind']} {g['id']} — only {g['done']} did it")
    return items


def _web_gaps_for(result, role):
    """web↔모바일 갭 중 이 역할이 빠뜨린 것. missing='mobile' 은 모바일 역할 전부가 빠뜨린 것이다."""
    out = []
    for g in result.get("parity_web") or []:
        if g["missing"] == role or (g["missing"] == "mobile" and role in util.MOBILE):
            out.append(f"{g['kind']} {g['id']} — {g['done']} did it (web↔mobile)")
    return out


def write_handoff(root, result, version):
    """루프 인계 — 다음 착수 프롬프트에 그대로 실린다."""
    roles = {}
    for r, e in result["roles"].items():
        rep = e.get("report") or {}
        roles[r] = {
            "not_done": rep.get("not_done") or [],
            "blocked": rep.get("blocked") or [],
            "unused": [i["id"] + " " + (i["const"] or i["label"]) for i in e["consumption"]["items"] if not i["used"]],
            "hardcodes": [f"{h['file']}:{h['line']} {h['kind']}" + (f" → use {h['token']}" if h.get("token") else "")
                          for h in e["hardcodes"]],
            "parity": [f"{g['kind']} {g['id']} — {g['done']} did it" for g in result["parity"] if g["missing"] == r]
            + _web_gaps_for(result, r),
            "untested": [i["id"] for i in e["test_coverage"]["items"] if not i["used"]][:20]
            if e["tests"]["source"] == "uncontracted" else [],
            "blockers": e["blockers"],
        }
    h = {"version": version, "score": result["score"], "verdict": result["verdict"], "roles": roles,
         "proposals": [{"role": r, "proposal": p} for r, e in result["roles"].items()
                       for p in ((e.get("report") or {}).get("proposals") or [])]}
    util.write_json(util.ho(root, util.HANDOFF), h)
    return h
