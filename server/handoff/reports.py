"""사람용 문서 · 에이전트용 프롬프트 — 전부 기계용 데이터에서 렌더링한다. 손으로 쓰지 않는다.

  docs/handoff-plan.md          계약 요약 (화면 · 토큰 · 라우트 · 스펙)
  docs/handoff-verify.md        검사 결과
  docs/handoff-screens/         디자인 원본 | iOS | Android 나란히 (스크린샷이 있을 때)
  summary(...)                  채팅에 보이는 요약 표 (md) — 디자인 출처 · 계약 · 선택한 인프라. 사람 판단 지점(④·⑦) 앞에 보인다
  checklist(...)                구현 뒤 투두식 정합성 체크리스트 (md) — 항목별 [x]/[ ] + 분석. ⑥·⑦ 에 보인다
대시보드 HTML 은 없다 — 사람은 채팅 표와 docs/ 의 md 만 본다.
에이전트가 읽는 것(착수 프롬프트·리포트 응답)은 영어 명령문이다 — 산문을 넣지 않는다.
"""
import html
import json
import os
import shutil

from . import checks, derive, flow, git, infra as infra_mod, leaks, util

SCREENS_DIR = "handoff-screens"


def _esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def _components_of(root, m):
    """매니페스트의 컴포넌트(확정 뒤에는 상세가 채워진 것), 확정 전이면 derived/components.json."""
    m = m or {}
    if m.get("confirmed"):
        p = os.path.join(util.design_dir(root), "handoff.manifest.json")
        doc = util.read_json(p) if os.path.isfile(p) else None
        if isinstance(doc, dict) and isinstance(doc.get("components"), list) and doc["components"]:
            return doc["components"]
    return (derive.read(root, "components.json") or {}).get("components") or []


def _write(root, name, text):
    p = os.path.join(root, util.DOCS_DIR, name)
    util.write_text(p, leaks.mask_all(text))
    return os.path.join(util.DOCS_DIR, name)


# ─────────────────────────── 계약 요약 (md) ───────────────────────────

def plan_doc(root, cfg, st, routes):
    m = st.get("manifest") or {}
    spec = util.read_spec(root) or {}
    L = [f"# 계약 v{st['version']} — 지문 {st['fingerprint']}", ""]
    L += ["## 화면 (design/)", "", "| id | 제목 | 파일 | 스크린샷 |", "|---|---|---|---|"]
    for s in m.get("screens", []):
        L.append(f"| `{s['id']}` | {s['title']} | {s['file'] or '-'} | {len(s.get('shots') or [])} |")
    L += ["", f"문서: {', '.join(f'`design/{d}`' for d in m.get('docs', [])) or '없음'}"
          + (f" · 대화 기록: {', '.join(f'`design/{c}`' for c in m['chats'])}" if m.get("chats") else ""), ""]
    toks = m.get("tokens") or {}
    L += [f"## 토큰 ({len(toks)}개)", ""]
    if toks:
        L += ["| 키 | 종류 | 값 |", "|---|---|---|"]
        for k, v in sorted(toks.items()):
            L.append(f"| `{k}` | {v['kind']} | `{v['value']}` |")
    else:
        L.append("토큰이 없다 — 치수·색 하드코딩 검출이 비활성이다.")
    L += ["", f"## API ({len(routes)}개) — api/openapi.yaml", "", "| 이름 | 메서드 | 경로 | 요약 |", "|---|---|---|---|"]
    for r in routes:
        L.append(f"| `{r['name']}` | {r['method']} | `{r['path']}` | {r['summary']} |")
    L += ["", "## 스펙 (사람이 결정)", ""]
    L.append(f"- 플랫폼: {', '.join(spec.get('platforms') or [])}")
    L.append(f"- 스택: {json.dumps(spec.get('stack') or {}, ensure_ascii=False)}")
    infra = dict(spec.get("infra") or {})
    pricing = infra.pop("pricing", None)
    L.append(f"- 인프라: {json.dumps(infra, ensure_ascii=False)}")
    if spec.get("divergences"):
        L.append(f"- 승인된 iOS/Android 차이: {', '.join(map(str, spec['divergences']))}")
    for row in _pricing_rows(pricing, infra.get("scale")):
        L.append(row)
    return _write(root, "handoff-plan.md", "\n".join(L) + "\n")


def _pricing_rows(pricing, sid):
    """사람이 고를 때 본 요금표 (infra.pricing) — md 표. 없으면 빈 목록."""
    if not isinstance(pricing, dict) or not isinstance(pricing.get("combos"), dict):
        return []
    out = ["", f"### 요금 확인 {pricing.get('checked') or '?'} ({infra_mod.COST_UNIT})", "",
           "| 조합 | 소규모 | 중규모 이상 | 출처 |", "|---|---|---|---|"]
    for cid, row in pricing["combos"].items():
        if not isinstance(row, dict):
            continue
        c = infra_mod.combo(cid)
        out.append(f"| {c['label'] if c else cid} | {row.get('small', '-')} | {row.get('medium_plus', '-')} | "
                   + " ".join(f"<{u}>" for u in (row.get("sources") or [])) + " |")
    return out


# ─────────────────────────── 착수 프롬프트 (영어) ───────────────────────────

RULES = """## Rules (server-enforced; refusals cite the ID)
W1 [BLOCK]  Write only inside your worktree, under your role path. Nowhere else.
W2 [BLOCK]  Never touch the main working tree or another role's worktree.
C1 [BLOCK]  design/ and api/ are read-only. A wrong contract → report.proposals, not an edit.
C2 [BLOCK]  shared/generated/ is read-only. Never edit or regenerate it.
C3 [SCORE]  Consume the generated constants (ApiRoutes.* Screens.* DesignTokens.*). Raw paths,
            hex colors and token-valued numbers cost score.
S1 [BLOCK]  No secret values in code or config. Use env vars; commit only *.example files.
S2 [BLOCK]  Never read or commit .env, keys, certificates, credentials — even transiently.
P1 [SCORE]  (apps) Consume the same contract items and tokens as the other platform. If a
            difference is unavoidable, report it in report.divergences {topic, reason}.
T1 [REJECT] A failing build is not done. Fix it or report status=blocked.
T2 [REPORT] Skipped/deleted tests and edited expectations are listed for the human.
T3 [SCORE]  Tests must reference the generated constants; a suite that touches none is not evidence.
R1 [BLOCK]  Run precheck(role) until it passes, then report(role, report). Reports with open
            blockers are refused unless status=blocked.
R2 [REJECT] status=blocked needs blocked[] entries with `tried` (what you attempted) and `error` (verbatim).
R3 [SELF]   Commit per unit of work with a clear subject. Test-first commits are noted in the verdict.
"""

REPORT_SHAPE = """{"status": "done|partial|blocked",
 "not_done": ["API-03 reason", ...], "blocked": [{"what": "...", "tried": ["..."], "error": "verbatim"}],
 "divergences": [{"topic": "...", "reason": "..."}], "proposals": ["contract change you need, with why"],
 "build": {"ok": true, "seconds": 0}, "tests": {"passed": 0, "failed": 0, "seconds": 0},
 "human_check": ["things a human must fill in or verify (e.g. env values)"]}"""


def kickoff(root, cfg, role, st, t, handoff):
    spec = util.read_spec(root) or {}
    m = st["manifest"]
    wt = util.worktree(root, role)
    role_path = cfg["roles"].get(role, role)
    plats = util.platforms(root)
    L = [f"# handoff build — role: {role} — contract v{st['version']}", "",
         f"Worktree: {wt}  (branch {git.branch(role)})",
         f"Role path: {role_path}/  — write only here [W1]", "",
         "## Read first (paths relative to the worktree)"]
    dv = os.path.join(util.design_dir(root), "derived")
    if os.path.isfile(os.path.join(dv, "intent.md")):
        L.append("- design/derived/intent.md   (the designer's own words, oldest first — binding decisions; read before anything else)")
    if os.path.isfile(os.path.join(dv, "entities.json")):
        L.append("- design/derived/entities.json   (data arrays + initial state from the prototype — the domain model and seed data)")
    if os.path.isfile(os.path.join(dv, "behavior.json")):
        L.append("- design/derived/behavior.json   (handlers → state keys they set, tab transitions, timers — implement the same transitions)")
    if os.path.isfile(os.path.join(util.design_dir(root), "handoff.manifest.json")):
        L.append("- design/handoff.manifest.json   (the full index: screens with their components/strings/icons, typed components, navigation graph, initial state, entity summary)")
    if os.path.isfile(os.path.join(dv, "components.json")) and role != "backend":
        L.append("- design/derived/components.json + navigation.json   (every interactive element typed — sheet/modal/popover/tab/button/toggle/input/slider/item/gesture — and the entry screen, tabs, transitions)")
    if os.path.isfile(os.path.join(dv, "rules.json")) and role != "backend":
        r = derive.read(root, "rules.json") or {}
        L.append("- design/derived/rules.json   (the prototype's lint rules — "
                 + ("no hex colors; " if r.get("no_hex") else "") + ("no raw px; " if r.get("no_px") else "")
                 + (f"allowed font families: {', '.join(r['fonts'])}; " if r.get("fonts") else "")
                 + "tokens and Strings.* are mandatory [C3])")
    if os.path.isfile(os.path.join(dv, "strings.json")) and role != "backend":
        for s in m.get("screens", []):
            if os.path.isfile(os.path.join(util.design_dir(root), derive.DERIVED_DIR, "layout", f"{s['id']}.json")):
                L.append(f"- design/derived/layout/{s['id']}.json   (screen `{s['id']}` as a lossless layout tree — BUILD FROM THIS: same nesting and order, "
                         "every `style` property carried over, tokens/strings/icons already named as constants, `when`/`items`/`on_*` are the "
                         "conditions, lists and handlers; `raw_text` marks copy that has no Strings key — keep it verbatim and list it in report.human_check)")
        L.append("- design/derived/strings.json + shared/generated/Strings.*   (every piece of copy, keyed by screen — never inline Korean text [C3]; "
                 "entries with {name} placeholders are format strings — substitute the named value at runtime, never retype the text)")
    if os.path.isfile(os.path.join(dv, "icons.json")) and role != "backend":
        L.append("- design/derived/icons/*.svg + shared/generated/Icons.*   (icon assets — import each SVG under its name; Lucide style)")
    for d in m.get("docs", []):
        L.append(f"- design/{d}   (developer notes from the designer — follow them)")
    for c in m.get("chats", [])[:5]:
        L.append(f"- design/{c}   (the conversation that produced the design — intent lives here; decisions in it are binding)")
    if role != "backend":
        for s in m["screens"]:
            L.append(f"- design/{s['file']}" + (f"#{s['anchor']}" if s.get("anchor") else "")
                     + f"   (screen `{s['id']}` — {s['title']}; the original markup the layout tree was derived from — open it only for a detail the tree does not settle)")
    else:
        L.append("- design/*.html   (screens — read them to understand what data each screen needs)")
    L.append(f"- api/openapi.yaml   (backend contract — {'implement every route' if role == 'backend' else 'call routes through ApiRoutes.*'})")
    ext = ".swift" if role == "ios" else ".kt"
    if role != "backend":
        L.append(f"- shared/generated/ApiRoutes{ext}, Screens{ext}"
                 + (f", DesignTokens{ext}" if m.get("tokens") else "")
                 + (f", Strings{ext}, Icons{ext}" if os.path.isdir(dv) else "")
                 + "   (consume; never edit) [C2][C3]")
    comps = _components_of(root, m)
    if comps and role != "backend":
        L += ["", "## Components (typed — build each as its type on the platform; overlays are NOT screens)"]
        for typ in derive.COMPONENT_TYPES:
            for c in (x for x in comps if x.get("type") == typ):
                extra = []
                if c.get("open"):
                    extra.append("opened by " + ", ".join(c["open"]))
                if c.get("close"):
                    extra.append("closed by " + ", ".join(c["close"]))
                if c.get("handler") and typ not in ("sheet", "modal", "popover"):
                    extra.append(f"handler {c['handler']}")
                if c.get("target"):
                    extra.append(f"→ {c['target']}")
                if c.get("bind"):
                    extra.append(f"binds {c['bind']}")
                if c.get("children"):
                    extra.append("contains " + ", ".join(c["children"]))
                L.append(f"- {typ:8} `{c['id']}` — {c.get('title') or c['id']} (in {c.get('screen') or 'shared'}"
                         + (f"; {'; '.join(extra)}" if extra else "") + ")")
    L += ["", "## Decisions (human-made — do not re-decide)"]
    stack = spec.get("stack") or {}
    infra = spec.get("infra") or {}
    if role == "backend":
        L.append(f"- Backend stack: {stack.get('backend')}")
        L.append(f"- Infra: db={infra.get('db')} · auth={infra.get('auth')} · hosting={infra.get('hosting')}")
        sc = infra_mod.scale(infra.get("scale"))
        if sc:
            L.append(f"- Expected scale: {sc['en']} (MAU {infra.get('mau') or sc['mau']} · DAU {infra.get('dau') or sc['dau']})"
                     f" — build for this: {sc['build']}")
        if infra.get("cost"):
            basis = (f"checked {infra['cost_checked']}" if infra.get("cost_checked")
                     else f"built-in estimate from {infra.get('cost_basis') or infra_mod.COST_BASIS}, not re-checked")
            L.append(f"- Monthly cost band the human accepted: {infra['cost']} ({basis}) — do not add paid services beyond the chosen "
                     "db/auth/hosting; anything extra goes into report.human_check with its price")
        if infra.get("env_vars"):
            L.append(f"- Env vars to expect (create .env.example with placeholders): {', '.join(map(str, infra['env_vars']))}")
        if infra.get("notes"):
            L.append(f"- Notes: {infra['notes']}")
    else:
        L.append(f"- Project structure: {stack.get(role + '_project') or 'existing'}")
        L.append(f"- Backend: {stack.get('backend')} · auth={infra.get('auth')}")
        L.append(f"- Platforms in this build: {', '.join(plats)}")
    if spec.get("divergences"):
        L.append(f"- Approved iOS/Android divergences (free): {', '.join(map(str, spec['divergences']))}")
    L += ["", "## Checklist (the server measures exactly these)"]
    its = checks.items(role, t)
    for i in (x for x in its if x["kind"] != "string"):
        L.append(f"- {i['id']}  {i['label']:<40} " + (f"→ {i['const']}" if i["const"] else "→ real route handler"))
    groups = {}
    for i in (x for x in its if x["kind"] == "string"):
        groups.setdefault(i["const"].rsplit(".", 1)[0], []).append(i)
    for grp, rows in groups.items():
        leaves = [r["const"].rsplit(".", 1)[1] for r in rows]
        L.append(f"- {rows[0]['id']}..{rows[-1]['id']}  {grp + '.*':<40} → every key ({len(rows)}: "
                 + ", ".join(leaves[:6]) + ("…" if len(leaves) > 6 else "") + ") — each one is a checklist item")
    if role != "backend" and m.get("tokens"):
        L.append(f"- TOK     {len(m['tokens'])} tokens → DesignTokens.* for every color/dimension that has one [C3]")
        L.append(f"- SHOT    optional: save screenshots to {wt}/.handoff/shots/<screenId>.png for the human compare page")
    if handoff and handoff.get("roles", {}).get(role):
        h = handoff["roles"][role]
        L += ["", f"## Carried from v{handoff.get('version')} (score {handoff.get('score')}) — close these first"]
        for k, title in (("blockers", "Blockers"), ("not_done", "Not done"), ("unused", "Unconsumed"),
                         ("hardcodes", "Hardcodes"), ("parity", "Parity gaps — the other platform did these"),
                         ("untested", "Untested contract items"), ("blocked", "Blocked last time")):
            vals = h.get(k) or []
            if vals:
                L.append(f"### {title}")
                L += [f"- {json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v}" for v in vals[:15]]
    L += ["", RULES, "## Finish",
          f"1. precheck(role=\"{role}\") — fix every blocker, rerun until PASS [R1]",
          f"2. report(role=\"{role}\", report=...) with this shape:", REPORT_SHAPE,
          "The server fills version and hashes; do not send them. Wall-clock seconds are welcome.", ""]
    return "\n".join(L)


# ─────────────────────────── 검사 결과 (md) ───────────────────────────

def verify_doc(root, result, version):
    L = [f"# 검사 v{version} — 점수 {result['score']}/{result['threshold']} → {result['verdict']}", ""]
    c = result["components"]
    L.append(f"- 소비 {c['consumption']} · 테스트 {c['tests'] if c['tests'] is not None else '못 잼'} · "
             f"파리티 {c['parity']} · 하드코딩 {c['hardcodes']} · 미승인 발산 {c['unapproved_divergences']} · "
             f"파리티 갭 {c['parity_gaps']}")
    if result["blockers"]:
        L += ["", "## 블로커"] + [f"- {b}" for b in result["blockers"]]
    for r, e in result["roles"].items():
        cons = e["consumption"]
        L += ["", f"## {r} ({e['role_path']}/) — 소비 {cons['used']}/{cons['total']} ({cons['rate']}%)"]
        rep = e.get("report") or {}
        L.append(f"- 리포트: {rep.get('status') or '없음'} · 테스트 {e['tests']['source']}"
                 + (f" {e['tests']['score']:.0f}" if e['tests']['score'] is not None else "")
                 + f" · 작성 순서 {e['test_provenance']['verdict']} · 계약 접촉 "
                   f"{e['test_coverage']['used']}/{e['test_coverage']['total']}")
        un = [i for i in cons["items"] if not i["used"]]
        if un:
            L.append("- 안 된 항목: " + ", ".join(f"{i['id']} {i['const'] or i['label']}" for i in un[:20]))
        for h in e["hardcodes"][:20]:
            L.append(f"- 하드코딩 {h['kind']} {h['file']}:{h['line']}" + (f" → {h['token']}" if h.get("token") else ""))
        for s in e["bypass"]["skips"][:10]:
            L.append(f"- 테스트 skip: {s['file']} — {s['text']}")
        for d in e["bypass"]["deleted_tests"]:
            L.append(f"- 테스트 삭제: {d}")
        for x in rep.get("human_check") or []:
            L.append(f"- 사람 확인: {x}")
        for p in rep.get("proposals") or []:
            L.append(f"- 계약 수정 제안: {p}")
        for b in rep.get("blocked") or []:
            L.append(f"- 막힘: {json.dumps(b, ensure_ascii=False)[:300]}")
    if result["parity"]:
        L += ["", "## 파리티 갭 (iOS ↔ Android)"]
        L += [f"- {g['kind']} {g['id']}: {g['done']} 만 했다 (빠진 쪽: {g['missing']})" for g in result["parity"]]
    return _write(root, "handoff-verify.md", "\n".join(L) + "\n")


# ─────────────────────────── 화면 대조 페이지 ───────────────────────────

def screens_page(root, st, roles):
    """<워크트리>/.handoff/shots/<screen>[__variant].png 를 모아 디자인 원본과 나란히 놓는다."""
    m = st.get("manifest") or {}
    out_dir = os.path.join(root, util.DOCS_DIR, SCREENS_DIR)
    shots = {}
    for r in roles:
        if r == "backend":
            continue
        sd = os.path.join(util.worktree(root, r), util.HO_DIR, util.SHOTS)
        if not os.path.isdir(sd):
            continue
        for n in sorted(os.listdir(sd)):
            if not n.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                continue
            sid = os.path.splitext(n)[0].split("__")[0]
            os.makedirs(os.path.join(out_dir, r), exist_ok=True)
            shutil.copyfile(os.path.join(sd, n), os.path.join(out_dir, r, n))
            shots.setdefault(sid, {}).setdefault(r, []).append(f"{r}/{n}")
    if not shots:
        return None
    util.write_text(os.path.join(out_dir, ".gitignore"), "*\n")
    apps = [r for r in roles if r != "backend"]
    rows = []
    for s in m.get("screens", []):
        cells = [f'<td><iframe src="../../design/{_esc(s["file"])}{"#" + _esc(s["anchor"]) if s.get("anchor") else ""}" '
                 f'loading="lazy"></iframe></td>' if s.get("file") else "<td>-</td>"]
        for r in apps:
            imgs = shots.get(s["id"], {}).get(r) or []
            cells.append("<td>" + ("".join(f'<img src="{_esc(i)}" alt="{_esc(s["id"])} {r}">' for i in imgs)
                                   or '<span class="none">없음</span>') + "</td>")
        rows.append(f"<tr><th>{_esc(s['id'])}<br><small>{_esc(s['title'])}</small></th>{''.join(cells)}</tr>")
    page = (f"<!doctype html><html lang=\"ko\"><meta charset=\"utf-8\"><title>화면 대조 v{st['version']}</title>"
            "<style>body{font:14px system-ui;margin:1rem}table{border-collapse:collapse}td,th{border:1px solid #ccc;"
            "padding:.5rem;vertical-align:top;text-align:left}iframe{width:390px;height:844px;border:0}"
            "img{max-width:390px;display:block;margin-bottom:.5rem}.none{color:#999}</style>"
            f"<h1>화면 대조 — 계약 v{st['version']}</h1><p>왼쪽이 디자인 원본(design/), 오른쪽이 각 앱의 스크린샷.</p>"
            "<table><tr><th>화면</th><th>디자인</th>" + "".join(f"<th>{r}</th>" for r in apps) + "</tr>"
            + "".join(rows) + "</table></html>")
    util.write_text(os.path.join(out_dir, "index.html"), page)
    return os.path.join(util.DOCS_DIR, SCREENS_DIR, "index.html")


# ─────────────────────────── 채팅 요약 표 · 체크리스트 (md) ───────────────────────────

def _md_table(rows, head=("항목", "값")):
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")
    return "\n".join([f"| {head[0]} | {head[1]} |", "|---|---|"] + [f"| {esc(k)} | {esc(v)} |" for k, v in rows])


def _domains(urls):
    return ", ".join(sorted({u.split("//")[-1].split("/")[0] for u in (urls or []) if isinstance(u, str)}))


def summary(root, st, routes=None):
    """사람이 확인하는 요약 — 디자인 출처(리소스 id · url) · 계약 · 플랫폼·스택 · 선택한 인프라. 스킬이 채팅에 표로 그대로 보인다."""
    m = st.get("manifest") or {}
    spec = util.read_spec(root) or {}
    infra = spec.get("infra") or {}
    stack = spec.get("stack") or {}
    src = st.get("design_source") or {}
    rows = []
    if src.get("url"):
        rows.append(("Claude Design 프로젝트", src["url"]))
    if src.get("project_id"):
        rows.append(("프로젝트 id", src["project_id"]))
    if src.get("path"):
        rows.append(("패키지", src["path"]))
    screens = m.get("screens") or []
    if screens:
        rows.append(("화면", f"{len(screens)}개 — " + ", ".join(f"{s['id']}" + (f" ({s['file']})" if s.get("file") else "") for s in screens)))
    comps = _components_of(root, m)
    if comps:
        rows.append(("컴포넌트", f"{len(comps)}개" + (" (사람 확정)" if m.get("components_confirmed") else "")))
    rows.append(("계약", f"v{st.get('version', 0)} · 지문 [{st.get('fingerprint')}] · API {len(routes or [])}개"))
    rows.append(("플랫폼", ", ".join(spec.get("platforms") or []) or "-"))
    rows.append(("백엔드 스택", stack.get("backend") or "-"))
    for k in ("ios_project", "android_project"):
        if stack.get(k):
            rows.append((f"{k.split('_')[0]} 프로젝트", stack[k]))
    sc = infra_mod.scale(infra.get("scale"))
    if sc:
        rows.append(("규모", f"{sc['label']} (MAU {infra.get('mau') or sc['mau']} · DAU {infra.get('dau') or sc['dau']})"))
    c = infra_mod.combo(infra.get("combo"))
    if c:
        rows.append(("인프라 조합", f"{c['label']} (`{c['id']}`)"))
    for k, lab in (("db", "DB"), ("auth", "인증"), ("hosting", "호스팅")):
        if infra.get(k):
            rows.append((lab, infra[k]))
    if infra.get("cost"):
        basis = (f"확인 {infra['cost_checked']}" if infra.get("cost_checked")
                 else f"내장 {infra.get('cost_basis') or infra_mod.COST_BASIS} 기준 · 미확인")
        dom = _domains(infra.get("cost_sources"))
        rows.append(("월 비용 (대략)", f"{infra['cost']} — {basis}" + (f" · {dom}" if dom else "")))
    if infra.get("env_vars"):
        rows.append(("환경 변수", ", ".join(map(str, infra["env_vars"]))))
    if infra.get("notes"):
        rows.append(("메모", infra["notes"]))
    if spec.get("divergences"):
        rows.append(("승인된 iOS/Android 차이", ", ".join(map(str, spec["divergences"]))))
    return {"rows": rows, "markdown": _md_table(rows)}


def checklist(result, version):
    """구현 뒤 투두식 정합성 체크리스트 — 역할별 소비 항목 [x]/[ ] · 하드코딩 · 테스트 우회 · 파리티 갭 · 블로커 + 분석 문단.
    스킬이 채팅에 그대로 보인다. 사실만 적는다 — 판단(승인)은 사람이 한다."""
    c = result["components"]
    items, L = [], [f"### 정합성 체크 v{version} — 점수 {result['score']}/{result['threshold']} → **{result['verdict']}**", ""]
    for r, e in result["roles"].items():
        cons = e["consumption"]
        rep = e.get("report") or {}
        L.append(f"**{r}** ({e['role_path']}/) — 소비 {cons['used']}/{cons['total']} · 리포트 {rep.get('status') or '없음'} · "
                 f"테스트 {e['tests']['source']}" + (f" {e['tests']['score']:.0f}" if e["tests"]["score"] is not None else ""))
        for i in cons["items"]:
            items.append({"role": r, "id": i["id"], "label": i["const"] or i["label"], "done": bool(i["used"])})
            L.append(f"- [{'x' if i['used'] else ' '}] {i['id']} `{i['const'] or i['label']}`")
        for h in e["hardcodes"]:
            L.append(f"- [ ] 하드코딩 {h['kind']} `{h['file']}:{h['line']}`" + (f" → `{h['token']}`" if h.get("token") else ""))
            items.append({"role": r, "id": "HARD", "label": f"{h['file']}:{h['line']}", "done": False})
        for sk in e["bypass"]["skips"]:
            L.append(f"- [ ] 테스트 skip `{sk['file']}` — {sk['text'][:80]}")
        for d in e["bypass"]["deleted_tests"]:
            L.append(f"- [ ] 테스트 삭제 `{d}`")
        for d in e["unapproved_divergences"]:
            L.append(f"- [ ] 미승인 발산: {(d.get('topic') if isinstance(d, dict) else d)}")
        for x in rep.get("human_check") or []:
            L.append(f"- [ ] 사람 확인: {x}")
        for b in e["blockers"]:
            L.append(f"- [ ] 블로커: {b}")
        L.append("")
    if result["parity"]:
        L.append("**파리티 (iOS ↔ Android)**")
        for g in result["parity"]:
            L.append(f"- [ ] {g['kind']} {g['id']} — {g['done']} 만 했다, **{g['missing']}** 가 빠짐")
        L.append("")
    # 분석 — 숫자에서 나오는 사실만
    A = ["**분석**"]
    A.append(f"- 소비 {c['consumption']} · 테스트 {c['tests'] if c['tests'] is not None else '못 잼'} · 파리티 {c['parity']} "
             f"(가중 합 {result['score']}, 임계 {result['threshold']})")
    undone = [i for i in items if not i["done"] and i["id"] != "HARD"]
    if undone:
        by = {}
        for i in undone:
            by.setdefault(i["role"], []).append(i["id"])
        A.append("- 안 된 계약 항목: " + " · ".join(f"{r} {len(v)}개 ({', '.join(v[:6])}{'…' if len(v) > 6 else ''})" for r, v in by.items()))
    if c["hardcodes"]:
        A.append(f"- 하드코딩 {c['hardcodes']}건 — 토큰·상수로 바꾸면 소비 점수가 오른다")
    if c["parity_gaps"] or c["unapproved_divergences"]:
        A.append(f"- 파리티 갭 {c['parity_gaps']} · 미승인 발산 {c['unapproved_divergences']} — 한쪽만 구현했거나 승인 없이 달리 만든 것")
    none_tests = [r for r, s_ in c["tests_source"].items() if s_ in ("none", "uncontracted")]
    if none_tests:
        A.append(f"- 테스트 증거 없음: {', '.join(none_tests)} ({'계약을 안 건드리는 스위트' if 'uncontracted' in c['tests_source'].values() else '리포트·서버 실행 둘 다 없음'})")
    if result["blockers"]:
        A.append("- 블로커 " + str(len(result["blockers"])) + "건 — 점수와 무관하게 pass 가 안 된다: " + "; ".join(result["blockers"][:5]))
    props = [(r, p_) for r, e in result["roles"].items() for p_ in ((e.get("report") or {}).get("proposals") or [])]
    if props:
        A.append("- 계약 수정 제안: " + " · ".join(f"[{r}] {p_}" for r, p_ in props[:5]))
    if result["verdict"] == "pass":
        A.append("- 결론: 계약 항목이 소비됐고 블로커가 없다. 위 [ ] 항목은 승인과 함께 예외로 받아들이는 것이다")
    else:
        A.append("- 결론: loop — 위 [ ] 항목이 다음 착수 프롬프트에 인계된다")
    L += A
    return {"items": items, "verdict": result["verdict"], "score": result["score"], "threshold": result["threshold"],
            "markdown": "\n".join(L)}
