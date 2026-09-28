"""판정 — 가중 점수 + 임계치 + 하드 블로커.

  점수 = w·소비 + w·테스트 + w·파리티      (잴 수 없는 성분은 빼고 정규화)
  소비   = 역할별 계약 소비율 − 하드코딩 감점
  테스트 = 서버 재실행 결과 > 자기신고. 계약 항목을 하나도 안 건드린 스위트는 증거가 아니라 제외
  파리티 = 100 − 벌점 × (미승인 발산 + 실측 갭)
  블로커가 하나라도 있으면 점수와 무관하게 loop.

검사는 두 층이다. static(소비 · 하드코딩 · 파리티 · 시크릿 · 위반 — 어디서든 잰다)과 runtime(서버가 돌린 테스트 ·
빌드 성공 — 툴체인이 있는 기기에서만). 통과했는데 어떤 역할에 runtime 증거가 없으면 판정은 pass 가 아니라
pass_static 이고, ship 이 그 역할을 runtime_pending 으로 남긴다.

착수 프롬프트의 체크리스트 · precheck · verify 가 전부 같은 함수를 쓴다 — 몰라서 틀리는 루프를 없앤다.
"""
import os

from . import checks, git, leaks, util

REPORT_STATUS = ("done", "partial", "blocked")
EVIDENCE = "evidence"      # .handoff/evidence/<role>.json — verify 때마다 남기는 정적 스냅샷(소비·토큰). 같은 계약 지문이면
                           # 상대 플랫폼이 이번 런에 없어도 파리티를 잰다 — iOS 와 Android 를 따로(다른 사이클·다른 기기) 돌릴 수 있다


def read_report(root, role):
    return util.read_json(util.ho(root, util.REPORTS, f"{role}.json"))


def _snapshot_of(e):
    return {"role": e["role"],
            "consumption": {"items": [{k: i.get(k) for k in ("id", "kind", "name", "label", "used", "const")} for i in e["consumption"]["items"]]},
            "tokens": sorted(e.get("tokens") or [])}


def write_snapshot(root, e, fingerprint):
    util.write_json(util.ho(root, EVIDENCE, f"{e['role']}.json"), {**_snapshot_of(e), "fingerprint": fingerprint, "at": util.now()})


def read_snapshot(root, role, fingerprint):
    """같은 계약 지문(design/+api/)의 스냅샷만 — 계약이 바뀌었으면 옛 구현과의 대조는 의미가 없다."""
    s = util.read_json(util.ho(root, EVIDENCE, f"{role}.json"))
    return s if isinstance(s, dict) and s.get("fingerprint") == fingerprint and s.get("role") == role else None


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

    build_ok = ((report or {}).get("build") or {}).get("ok") if isinstance((report or {}).get("build"), dict) else None
    server_ran = bool(verify and verify.get("ran"))
    evidence = {"static": True,
                "runtime": server_ran or build_ok is True,      # 서버가 돌렸거나, 빌더가 빌드 성공을 보고했거나
                "runtime_source": "server" if server_ran else "self-reported" if build_ok is True else "none",
                "build_ok": build_ok}

    return {"role": role, "role_path": role_path, "blockers": blockers,
            "consumption": cons, "hardcodes": hard, "tokens": tokens, "bypass": bypass,
            "test_provenance": prov, "test_coverage": cov,
            "tests": {"score": tests_score, "source": src, "detail": verify}, "evidence": evidence,
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
    fp = util.fingerprint(root)
    for r in roles:
        write_snapshot(root, per[r], fp)                    # 다음 런(다른 플랫폼·다른 기기)의 파리티 상대가 된다
    mobile = [r for r in roles if r in util.MOBILE]
    snaps = {}                                               # 이번 런에 없는 앱 역할의 스냅샷 (같은 지문일 때만)
    for r in (*util.MOBILE, "web"):
        if r not in roles:
            s = read_snapshot(root, r, fp)
            if s:
                snaps[r] = s
    if len(mobile) == 2:
        gaps = checks.parity(per[mobile[0]], per[mobile[1]], approved)                        # iOS ↔ Android — 그대로
    elif len(mobile) == 1 and any(m in snaps for m in util.MOBILE):
        other = [m for m in util.MOBILE if m != mobile[0]][0]
        gaps = [{**g, "snapshot": other, "snapshot_at": snaps[other]["at"]} for g in checks.parity(per[mobile[0]], snaps[other], approved)]
    else:
        gaps = []
    mobiles_for_web = [per[r] for r in mobile] + [snaps[m] for m in util.MOBILE if m in snaps]
    web_eval = per["web"] if "web" in roles else snaps.get("web")
    if web_eval and mobiles_for_web:                                                         # web ↔ 모바일 합집합 — web 이 (스냅샷으로라도) 있을 때만
        used_snaps = [m for m in util.MOBILE if m in snaps] + (["web"] if "web" not in roles else [])
        web_gaps = [({**g, "snapshot": used_snaps} if used_snaps else g)
                    for g in checks.parity_web(web_eval, mobiles_for_web, approved, t.get("gesture_handlers"))]
    else:
        web_gaps = []
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
    runtime_pending = [r for r in roles if not per[r]["evidence"]["runtime"]]
    ok = not blockers and score >= sc["threshold"]
    verdict = "loop" if not ok else "pass_static" if runtime_pending else "pass"
    return {"roles": per, "score": round(score, 1), "threshold": sc["threshold"], "verdict": verdict,
            "runtime_pending": runtime_pending, "parity_snapshots": {r: s["at"] for r, s in snaps.items()},
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
        if not e.get("evidence", {}).get("runtime", True):
            items.append(f"[{r}] runtime unchecked: no build/test evidence on this machine — static checks only; "
                         "a machine with the toolchain verifies it later (runtime_pending)")
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
         "runtime_pending": result.get("runtime_pending") or [],
         "proposals": [{"role": r, "proposal": p} for r, e in result["roles"].items()
                       for p in ((e.get("report") or {}).get("proposals") or [])]}
    util.write_json(util.ho(root, util.HANDOFF), h)
    return h
