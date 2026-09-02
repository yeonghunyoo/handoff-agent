"""사람용 문서 · 에이전트용 프롬프트 — 전부 기계용 데이터에서 렌더링한다. 손으로 쓰지 않는다.

  docs/handoff-plan.md          계약 요약 (화면 · 토큰 · 라우트 · 스펙)
  docs/handoff-verify.md        검사 결과
  docs/handoff-dashboard.html   사람이 보는 정본 (아티팩트로 발행)
  docs/handoff-screens/         디자인 원본 | iOS | Android 나란히 (스크린샷이 있을 때)
에이전트가 읽는 것(착수 프롬프트·리포트 응답)은 영어 명령문이다 — 산문을 넣지 않는다.
"""
import base64
import html
import json
import os
import shutil

from . import checks, flow, git, leaks, util

SCREENS_DIR = "handoff-screens"


def _esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def _write(root, name, text):
    p = os.path.join(root, util.DOCS_DIR, name)
    util.write_text(p, leaks.mask(text))
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
    L.append(f"- 인프라: {json.dumps(spec.get('infra') or {}, ensure_ascii=False)}")
    if spec.get("divergences"):
        L.append(f"- 승인된 iOS/Android 차이: {', '.join(map(str, spec['divergences']))}")
    return _write(root, "handoff-plan.md", "\n".join(L) + "\n")


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
    for d in m.get("docs", []):
        L.append(f"- design/{d}   (developer notes from the designer — follow them)")
    for c in m.get("chats", [])[:5]:
        L.append(f"- design/{c}   (the conversation that produced the design — intent lives here; decisions in it are binding)")
    if role != "backend":
        for s in m["screens"]:
            L.append(f"- design/{s['file']}" + (f"#{s['anchor']}" if s.get("anchor") else "")
                     + f"   (screen `{s['id']}` — {s['title']}; the source of truth for layout, copy, states)")
    else:
        L.append("- design/*.html   (screens — read them to understand what data each screen needs)")
    L.append(f"- api/openapi.yaml   (backend contract — {'implement every route' if role == 'backend' else 'call routes through ApiRoutes.*'})")
    ext = ".swift" if role == "ios" else ".kt"
    if role != "backend":
        L.append(f"- shared/generated/ApiRoutes{ext}, Screens{ext}"
                 + (f", DesignTokens{ext}" if m.get("tokens") else "")
                 + "   (consume; never edit) [C2][C3]")
    L += ["", "## Decisions (human-made — do not re-decide)"]
    stack = spec.get("stack") or {}
    infra = spec.get("infra") or {}
    if role == "backend":
        L.append(f"- Backend stack: {stack.get('backend')}")
        L.append(f"- Infra: db={infra.get('db')} · auth={infra.get('auth')} · hosting={infra.get('hosting')}")
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
    for i in checks.items(role, t):
        L.append(f"- {i['id']}  {i['label']:<40} " + (f"→ {i['const']}" if i["const"] else "→ real route handler"))
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


# ─────────────────────────── 대시보드 ───────────────────────────

_CSS = """
:root{--bg:#fafaf8;--fg:#222;--mut:#666;--line:#ddd;--card:#fff;--ok:#2a7;--bad:#c33;--warn:#c80;--acc:#2563eb}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#151515;--fg:#eee;--mut:#999;--line:#333;--card:#1e1e1e}}
:root[data-theme="dark"]{--bg:#151515;--fg:#eee;--mut:#999;--line:#333;--card:#1e1e1e}
body{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif;margin:0;padding:1.5rem;max-width:1100px}
h1{font-size:1.4rem;margin:0 0 .25rem}h2{font-size:1.1rem;margin:2rem 0 .5rem;border-bottom:1px solid var(--line)}
.meta{color:var(--mut)}.phases{display:flex;flex-wrap:wrap;gap:.35rem;margin:1rem 0}
.ph{padding:.2rem .6rem;border:1px solid var(--line);border-radius:999px;color:var(--mut)}
.ph.cur{background:var(--acc);color:#fff;border-color:var(--acc)}.ph.done{color:var(--fg)}
.warn{background:rgba(200,128,0,.12);border-left:3px solid var(--warn);padding:.5rem .8rem;margin:.4rem 0}
.bad{color:var(--bad)}.ok{color:var(--ok)}table{border-collapse:collapse;width:100%;margin:.5rem 0}
td,th{border-bottom:1px solid var(--line);padding:.3rem .5rem;text-align:left;vertical-align:top}
code{background:rgba(127,127,127,.15);padding:0 .25rem;border-radius:3px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:.8rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:.6rem}
.card img{width:100%;border-radius:4px;border:1px solid var(--line)}.card .t{font-weight:600}.card .s{color:var(--mut);font-size:.85em}
.score{font-size:2rem;font-weight:700}.badge{display:inline-block;padding:.1rem .5rem;border-radius:4px;color:#fff}
.badge.pass{background:var(--ok)}.badge.loop{background:var(--bad)}details{margin:.4rem 0}summary{cursor:pointer}
.sw{display:inline-block;width:.9em;height:.9em;border:1px solid var(--line);vertical-align:middle;margin-right:.3rem}
pre{white-space:pre-wrap;background:var(--card);border:1px solid var(--line);padding:.6rem;border-radius:6px}
"""


def _img_data(path, budget):
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, budget
    if size > budget or size > 600_000:
        return None, budget
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    mime = {"jpg": "jpeg"}.get(ext, ext)
    with open(path, "rb") as f:
        return f"data:image/{mime};base64,{base64.b64encode(f.read()).decode()}", budget - size


def dashboard(root, cfg, st, routes=None, result=None, handoff=None):
    m = st.get("manifest") or {}
    spec = util.read_spec(root) or {}
    P = [f"<title>handoff — {_esc(os.path.basename(root))}</title><style>{_CSS}</style>",
         f"<h1>{_esc(os.path.basename(root))} — {_esc(st['label'])}</h1>",
         f"<div class=meta>계약 v{st['version']} · 지문 <b>[{st['fingerprint']}]</b> · 사이클 {st['cycle']} · {util.now()}</div>",
         "<div class=phases>" + "".join(
             f'<span class="ph {"cur" if p == st["phase"] else ("done" if flow.idx(p) < flow.idx(st["phase"]) else "")}">'
             f'{_esc(flow.LABELS[p])}</span>' for p in flow.PHASES) + "</div>"]
    if st["human"]:
        P.append(f"<div class=warn>✋ 사람 판단 지점 — {_esc(st['next'])}</div>")
    for w in st["warnings"]:
        P.append(f"<div class=warn>{_esc(w)}</div>")

    # 화면
    P.append(f"<h2>화면 — {len(m.get('screens', []))}개 (design/)</h2><div class=grid>")
    budget = 6_000_000
    for s in m.get("screens", []):
        img = None
        for shot in s.get("shots") or []:
            img, budget = _img_data(os.path.join(util.design_dir(root), shot), budget)
            if img:
                break
        P.append("<div class=card>" + (f'<img src="{img}" alt="">' if img else "")
                 + f'<div class=t>{_esc(s["title"])}</div><div class=s><code>Screens.{_esc(s["id"])}</code>'
                 + (f' · design/{_esc(s["file"])}' if s.get("file") else "") + "</div></div>")
    P.append("</div>")
    if m.get("docs"):
        P.append("<p class=meta>문서: " + ", ".join(f"<code>design/{_esc(d)}</code>" for d in m["docs"]) + "</p>")

    # 토큰
    toks = m.get("tokens") or {}
    P.append(f"<h2>토큰 — {len(toks)}개</h2>")
    if toks:
        P.append("<details><summary>펼치기</summary><table><tr><th>키</th><th>종류</th><th>값</th></tr>")
        for k, v in sorted(toks.items()):
            sw = (f'<span class=sw style="background:{_esc(v["value"])}"></span>'
                  if v["kind"] == "color" and str(v["value"]).startswith("#") else "")
            P.append(f"<tr><td><code>{_esc(k)}</code></td><td>{v['kind']}</td><td>{sw}<code>{_esc(v['value'])}</code></td></tr>")
        P.append("</table></details>")
    else:
        P.append("<p class=meta>토큰이 없다 — 색·치수 하드코딩 검출이 비활성이다.</p>")

    # API
    routes = routes or []
    P.append(f"<h2>API — {len(routes)}개 (api/openapi.yaml)</h2>")
    if routes:
        P.append("<table><tr><th>메서드</th><th>경로</th><th>요약</th><th>상수</th></tr>")
        for r in routes:
            P.append(f"<tr><td>{r['method']}</td><td><code>{_esc(r['path'])}</code></td><td>{_esc(r['summary'])}</td>"
                     f"<td><code>ApiRoutes.{_esc(r['name'])}</code></td></tr>")
        P.append("</table>")
    else:
        P.append("<p class=meta>아직 없다.</p>")

    # 스펙
    P.append("<h2>스펙 · 인프라 (사람이 결정)</h2><ul>")
    P.append(f"<li>플랫폼: {_esc(', '.join(spec.get('platforms') or []) or '-')}</li>")
    for k, v in (spec.get("stack") or {}).items():
        P.append(f"<li>스택 {_esc(k)}: {_esc(v)}</li>")
    for k, v in (spec.get("infra") or {}).items():
        P.append(f"<li>인프라 {_esc(k)}: {_esc(json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)}</li>")
    if spec.get("divergences"):
        P.append(f"<li>승인된 플랫폼 차이: {_esc(', '.join(map(str, spec['divergences'])))}</li>")
    P.append("</ul>")

    # 검사
    if result:
        c = result["components"]
        P.append(f"<h2>검사 v{st['version']}</h2><div><span class=score>{result['score']}</span> / {result['threshold']} "
                 f"<span class='badge {result['verdict']}'>{result['verdict']}</span></div>")
        P.append(f"<p>소비 {c['consumption']} · 테스트 {c['tests'] if c['tests'] is not None else '못 잼'} · "
                 f"파리티 {c['parity']} · 하드코딩 {c['hardcodes']} · 미승인 발산 {c['unapproved_divergences']} · "
                 f"파리티 갭 {c['parity_gaps']}</p>")
        if result["blockers"]:
            P.append("<ul>" + "".join(f"<li class=bad>{_esc(b)}</li>" for b in result["blockers"]) + "</ul>")
        for r, e in result["roles"].items():
            cons = e["consumption"]
            rep = e.get("report") or {}
            P.append(f"<details open><summary><b>{r}</b> — {_esc(e['role_path'])}/ · 소비 {cons['used']}/{cons['total']} "
                     f"({cons['rate']}%) · 리포트 {_esc(rep.get('status') or '없음')} · 테스트 {_esc(e['tests']['source'])}"
                     f" · 작성 순서 {_esc(e['test_provenance']['verdict'])}</summary><ul>")
            for i in [i for i in cons["items"] if not i["used"]][:30]:
                P.append(f"<li class=bad>안 됨 {i['id']} <code>{_esc(i['const'] or i['label'])}</code></li>")
            for h in e["hardcodes"][:30]:
                P.append(f"<li>하드코딩 {h['kind']} <code>{_esc(h['file'])}:{h['line']}</code>"
                         + (f" → <code>{_esc(h['token'])}</code>" if h.get("token") else "") + "</li>")
            for s in e["bypass"]["skips"][:10]:
                P.append(f"<li>테스트 skip <code>{_esc(s['file'])}</code> {_esc(s['text'])}</li>")
            for d in e["bypass"]["deleted_tests"]:
                P.append(f"<li class=bad>테스트 삭제 <code>{_esc(d)}</code></li>")
            for d in e["unapproved_divergences"]:
                P.append(f"<li>미승인 발산: {_esc(json.dumps(d, ensure_ascii=False))}</li>")
            for x in rep.get("human_check") or []:
                P.append(f"<li>사람 확인: {_esc(x)}</li>")
            for p in rep.get("proposals") or []:
                P.append(f"<li>계약 수정 제안: {_esc(p)}</li>")
            for b in rep.get("blocked") or []:
                P.append(f"<li class=bad>막힘: <pre>{_esc(json.dumps(b, ensure_ascii=False, indent=1)[:800])}</pre></li>")
            P.append("</ul></details>")
        if result["parity"]:
            P.append("<h3>파리티 갭</h3><ul>" + "".join(
                f"<li>{_esc(g['kind'])} {_esc(g['id'])} — {g['done']} 만 했다 (빠진 쪽 <b>{g['missing']}</b>)</li>"
                for g in result["parity"]) + "</ul>")
        page = os.path.join(root, util.DOCS_DIR, SCREENS_DIR, "index.html")
        if os.path.isfile(page):
            P.append(f"<p>화면 대조(사진): <code>{util.DOCS_DIR}/{SCREENS_DIR}/index.html</code> — 레포에서 연다 (아티팩트에 안 실린다)</p>")
    if handoff and st["phase"] == "build":
        P.append(f"<h2>인계 (v{handoff.get('version')} → 이번 루프)</h2><pre>"
                 + _esc(json.dumps(handoff.get("roles"), ensure_ascii=False, indent=1)[:4000]) + "</pre>")
    P.append("<p class=meta>이 문서는 서버가 렌더링한다 — 승인은 elicitation 또는 터미널에서만 받는다.</p>")
    return _write(root, "handoff-dashboard.html", "\n".join(P))
