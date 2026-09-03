"""실측 — 코드와 diff 에서 직접 잰다. 리포트의 자기신고는 참고 자료일 뿐이다.

  targets      계약 항목 (라우트 · 화면 · 토큰) — 착수 체크리스트와 검사가 같은 목록을 쓴다
  consumption  역할별 계약 소비율
  hardcodes    토큰이 있는데 숫자/hex 로 박은 곳
  parity       iOS ↔ Android 한쪽만 한 것
  violations   보호 구역 변경 · 담당 밖 쓰기
  tests        우회(skip·삭제) · 작성 순서 · 계약 접촉 · 재실행
  leaks        코드 · 민감 파일 · 커밋 이력의 시크릿
"""
import fnmatch
import os
import re
import subprocess
import time

from . import api, derive, design, gen, git, leaks, util

CODE_EXT = {".swift", ".kt", ".kts", ".java", ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rb",
            ".rs", ".cs", ".dart", ".m", ".mm", ".xml", ".yaml", ".yml", ".toml", ".json", ".sql"}
SKIP_DIRS = {".git", ".handoff", "node_modules", "build", ".build", "dist", "DerivedData", "Pods",
             ".gradle", "__pycache__", ".venv", "venv", "generated"}
_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?\b")
_DIM_RES = [
    re.compile(r"\b(\d+(?:\.\d+)?)\.(?:dp|sp)\b"),
    re.compile(r"\.(?:padding|frame|cornerRadius|font|spacing|offset|lineSpacing)\([^)]*?\b(\d+(?:\.\d+)?)\b"),
    re.compile(r"\b(?:spacing|padding|height|width|radius|size)\s*[:=]\s*(\d+(?:\.\d+)?)\b"),
    re.compile(r"\bCGFloat\((\d+(?:\.\d+)?)\)"),
    re.compile(r"\bRoundedCornerShape\((\d+(?:\.\d+)?)"),
]
_KO_LIT = re.compile(r"[\"'][^\"'\n]*[가-힣][^\"'\n]*[\"']")
# 치수 토큰은 종류가 맞는 문맥에서만 잡는다 — radius 8 이 점 크기 8 로 쓰인 것은 하드코딩이 아니다
def _dim_kind(key):
    """토큰 키 → 문맥 종류. radius.* · corner.* → radius, space.* · spacing.* · gap.* → space, 그 외 None(어디서든 잡는다)."""
    k = key.lower()
    if "radius" in k or k.startswith("corner"):
        return "radius"
    if k.startswith(("space", "spacing", "gap", "inset")):
        return "space"
    return None


_DIM_CTX = {
    "radius": re.compile(r"cornerRadius|RoundedRectangle|RoundedCornerShape|clipShape|\.clip\(|shape\s*=|[Rr]adius"),
    "space": re.compile(r"padding|spacing|spacedBy|Spacer|offset|lineSpacing|gap|inset"),
}
_FONT_RE = re.compile(r"(?:Font\.custom|fontFamily\s*=\s*FontFamily|FontFamily\(|Font\(name:)\s*\(?\s*[\"']([A-Za-z][\w -]*)[\"']")
_SKIP_RE = re.compile(r"(@pytest\.mark\.skip|pytest\.skip|@unittest\.skip|XCTSkip|@Ignore\b|"
                      r"\.skip\(|\bxit\(|\bxdescribe\(|it\.skip|test\.skip|@Disabled\b|t\.Skip\()")


# ─────────────────────────── 계약 항목 ───────────────────────────

def targets(root):
    m = design.manifest(root) or {"screens": [], "tokens": {}}
    text = util.read_text(util.api_path(root))
    try:
        rs = api.validate(text) if text.strip() else []
    except api.ApiError:
        rs = []
    consts = gen.token_consts(m["tokens"])
    tokens = {k: {**v, "const": consts[k]} for k, v in m["tokens"].items()}
    icons = (derive.read(root, "icons.json") or {}).get("icons") or []
    strings = (derive.read(root, "strings.json") or {}).get("strings") or []
    rules = derive.read(root, "rules.json") or {}
    handlers = sorted(((derive.read(root, "behavior.json") or {}).get("handlers") or {}).keys())
    return {"routes": rs, "screens": m["screens"], "tokens": tokens, "icons": icons, "strings": strings, "rules": rules,
            "handlers": handlers}


def string_const(row):
    """strings.json 행 → 생성 상수 이름 (gen._string_tree 와 같은 규칙)."""
    grp, _, leaf = row["key"].partition(".")
    return f"Strings.{gen.ident(grp, upper=True)}.{gen.ident(leaf.replace('.', ' '))}"


def items(role, t):
    """역할이 소비해야 하는 항목. [{id, kind, label, const, rx}] — id 는 리포트·인계가 같은 이름으로 말한다."""
    out = []
    w = max(2, len(str(len(t["routes"]))))
    for i, r in enumerate(t["routes"], 1):
        rid = f"API-{i:0{w}d}"
        label = f"{r['method']} {r['path']}"
        if role == "backend":
            out.append({"id": rid, "kind": "route", "label": label, "const": None,
                        "rx": api.path_regex(r["path"]), "name": r["name"]})
        else:
            const = f"ApiRoutes.{r['name']}"
            out.append({"id": rid, "kind": "route", "label": label, "const": const,
                        "rx": re.compile(re.escape(const) + r"\b"), "name": r["name"]})
    if role != "backend":
        w = max(2, len(str(len(t["screens"]))))
        for i, s in enumerate(t["screens"], 1):
            const = f"Screens.{s['id']}"
            out.append({"id": f"SCR-{i:0{w}d}", "kind": "screen", "label": s["title"], "const": const,
                        "rx": re.compile(re.escape(const) + r"\b|[\"']" + re.escape(s["id"]) + r"[\"']"),
                        "name": s["id"]})
        icons = t.get("icons") or []
        w = max(2, len(str(len(icons))))
        for i, ic in enumerate(icons, 1):
            const = f"Icons.{gen.ident(ic['name'])}"
            out.append({"id": f"ICN-{i:0{w}d}", "kind": "icon", "label": ic["name"], "const": const,
                        "rx": re.compile(re.escape(const) + r"\b|[\"']" + re.escape(ic["name"]) + r"[\"']"),
                        "name": ic["name"]})
        # 문구 — 화면의 모든 카피를 Strings.* 로 썼는가 (빼먹은 문구가 보인다). 그룹별로 id 가 이어지게 정렬한다
        strs = sorted(t.get("strings") or [], key=lambda r: (r["key"].partition(".")[0], r["key"]))
        w = max(2, len(str(len(strs))))
        for i, r in enumerate(strs, 1):
            const = string_const(r)
            out.append({"id": f"STR-{i:0{w}d}", "kind": "string", "label": f"{r['screen']}: {r['text'][:40]}", "const": const,
                        "rx": re.compile(re.escape(const) + r"\b"), "name": r["key"]})
        # 핸들러 — behavior.json 의 핸들러 이름이 코드에 있는가 (동작이 구현됐는지의 최소 증거)
        hs = t.get("handlers") or []
        w = max(2, len(str(len(hs))))
        for i, h in enumerate(hs, 1):
            out.append({"id": f"HND-{i:0{w}d}", "kind": "handler", "label": f"handler {h}", "const": h,
                        "rx": re.compile(r"\b" + re.escape(h) + r"\b"), "name": h})
    return out


# ─────────────────────────── 코드 순회 ───────────────────────────

def is_test(rel, cfg):
    rel = rel.replace(os.sep, "/")
    return any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch("/" + rel, g) for g in cfg["test_globs"])


def iter_code(tree, role_path, cfg, tests=None):
    base = os.path.join(tree, role_path)
    if not os.path.isdir(base):
        return
    for d, dirs, names in os.walk(base):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith(".")]
        for n in names:
            if os.path.splitext(n)[1].lower() not in CODE_EXT:
                continue
            p = os.path.join(d, n)
            rel = os.path.relpath(p, tree).replace(os.sep, "/")
            t = is_test(rel, cfg)
            if tests is True and not t or tests is False and t:
                continue
            try:
                if os.path.getsize(p) > 2_000_000:
                    continue
            except OSError:
                continue
            yield rel, util.read_text(p)


def _scan(tree, role_path, cfg, its, tests):
    used = {i["id"]: False for i in its}
    for _, text in iter_code(tree, role_path, cfg, tests=tests):
        for i in its:
            if not used[i["id"]] and i["rx"].search(text):
                used[i["id"]] = True
    rows = [{"id": i["id"], "kind": i["kind"], "label": i["label"], "const": i["const"],
             "name": i["name"], "used": used[i["id"]]} for i in its]
    n = sum(1 for r in rows if r["used"])
    return {"items": rows, "used": n, "total": len(rows),
            "rate": round(100.0 * n / len(rows), 1) if rows else 100.0}


def consumption(tree, role_path, cfg, role, t):
    return _scan(tree, role_path, cfg, items(role, t), tests=False)


def test_coverage(tree, role_path, cfg, role, t):
    return _scan(tree, role_path, cfg, items(role, t), tests=True)


def token_usage(tree, role_path, cfg, t):
    """참조된 토큰 키 목록 — 파리티 재료."""
    consts = {v["const"]: k for k, v in t["tokens"].items()}
    if not consts:
        return []
    rx = re.compile(r"\bDesignTokens(?:\.[A-Za-z_]\w*)+")
    found = set()
    for _, text in iter_code(tree, role_path, cfg, tests=False):
        for m in rx.findall(text):
            if m in consts:
                found.add(consts[m])
    return sorted(found)


def hardcodes(tree, role_path, cfg, role, t):
    """토큰에 이름이 있는 값을 숫자·hex 로 박은 곳. 토큰에 없는 값은 잡지 않는다 (오탐 방지)."""
    if role == "backend":
        return []
    colors = {}
    dims = {}
    for k, v in t["tokens"].items():
        if v["kind"] == "color" and isinstance(v["value"], str) and v["value"].startswith("#"):
            colors.setdefault(v["value"].lower()[:7], k)
        elif v["kind"] == "dimension":
            dims.setdefault(float(v["value"]), k)
    paths = [r["path"] for r in t["routes"]]
    have_strings = bool(t.get("strings"))
    fonts = [f.lower() for f in (t.get("rules") or {}).get("fonts") or []]
    out = []
    for rel, text in iter_code(tree, role_path, cfg, tests=False):
        if not rel.lower().endswith((".swift", ".kt", ".kts", ".java", ".dart", ".ts", ".tsx", ".js", ".jsx")):
            continue
        for ln, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if s.startswith(("//", "*", "/*", "#")):
                continue
            for m in _HEX_RE.findall(line):
                key = m.lower()[:7]
                if key in colors:
                    out.append({"file": rel, "line": ln, "kind": "hex-color", "token": colors[key],
                                "text": s[:120]})
            for rx in _DIM_RES:
                hit = False
                for m in rx.findall(line):
                    if float(m) in dims and float(m) not in (0.0, 1.0):
                        ctx = _DIM_CTX.get(_dim_kind(dims[float(m)]))
                        if ctx and not ctx.search(line):
                            continue                       # 종류가 다른 문맥 — 값이 같을 뿐이다
                        out.append({"file": rel, "line": ln, "kind": "raw-dimension",
                                    "token": dims[float(m)], "text": s[:120]})
                        hit = True
                        break
                if hit:
                    break
            for p in paths:
                if f'"{p}"' in line or f"'{p}'" in line:
                    out.append({"file": rel, "line": ln, "kind": "raw-path", "token": None, "text": s[:120]})
            if have_strings and _KO_LIT.search(line):
                out.append({"file": rel, "line": ln, "kind": "raw-string", "token": "Strings.*", "text": s[:120]})
            if fonts:
                for fm in _FONT_RE.findall(line):
                    if fm.lower() not in fonts and fm.lower() not in ("system", "systemui", "sans-serif", "monospace"):
                        out.append({"file": rel, "line": ln, "kind": "raw-font", "token": "/".join(fonts), "text": s[:120]})
    # 같은 줄 중복 제거
    seen, uniq = set(), []
    for h in out:
        k = (h["file"], h["line"], h["kind"])
        if k not in seen:
            seen.add(k)
            uniq.append(h)
    return uniq


def parity(a, b, approved_topics):
    """두 앱의 소비·토큰을 대조한다. [{kind, id, done, missing}]"""
    gaps = []
    ua = {i["id"]: i for i in a["consumption"]["items"]}
    ub = {i["id"]: i for i in b["consumption"]["items"]}
    for iid in sorted(set(ua) | set(ub)):
        x, y = ua.get(iid), ub.get(iid)
        if not x or not y or x["used"] == y["used"]:
            continue
        item = x if x["used"] else y
        if _covered(item["name"], approved_topics) or _covered(item["label"], approved_topics):
            continue
        done, missing = (a["role"], b["role"]) if x["used"] else (b["role"], a["role"])
        gaps.append({"kind": item["kind"], "id": f"{iid} {item['name']}", "done": done, "missing": missing})
    ta, tb = set(a["tokens"]), set(b["tokens"])
    for tok in sorted(ta ^ tb):
        if _covered(tok, approved_topics):
            continue
        done, missing = (a["role"], b["role"]) if tok in ta else (b["role"], a["role"])
        gaps.append({"kind": "token", "id": tok, "done": done, "missing": missing})
    return gaps


def _covered(name, topics):
    n = str(name).lower()
    return any(t and (t.lower() in n or n in t.lower()) for t in topics)


# ─────────────────────────── 브랜치 · 위반 ───────────────────────────

def changed(root, role):
    """브랜치가 바꾼 파일 (커밋 + 워크트리 미커밋)."""
    b = git.branch(role)
    files = set(git.changed_files(root, b)) if git.branch_exists(root, b) else set()
    wt = util.worktree(root, role)
    if os.path.isdir(wt):
        files |= {p for _, p in git.status_lines(wt)}
    return sorted(f for f in files if f)


def violations(root, cfg, role, files):
    role_path = cfg["roles"].get(role, role).strip("/")
    protected, trespass = [], []
    for f in files:
        if f.startswith((util.DESIGN_DIR + "/", util.API_DIR + "/")) or f.startswith(util.HO_DIR + "/"):
            protected.append(f)
        elif f.startswith(util.GEN_DIR + "/"):
            continue                              # 뼈대 — 손댔는지는 drift 가 바이트로 본다
        elif not f.startswith(role_path + "/"):
            trespass.append(f)
    return {"protected": protected, "trespass": trespass}


def skeleton_drift(root, cfg, role, version):
    wt = util.worktree(root, role)
    if not os.path.isdir(wt):
        return []
    m = design.manifest(root)
    if not m:
        return []
    try:
        rs = api.validate(util.read_text(util.api_path(root)))
    except api.ApiError:
        return []
    return gen.drift(wt, gen.expected(root, cfg, version, m, rs))


# ─────────────────────────── 테스트 ───────────────────────────

def test_bypass(root, cfg, role):
    b = git.branch(role)
    if not git.branch_exists(root, b):
        return {"skips": [], "deleted_tests": []}
    diff = git.diff_text(root, b)
    skips, deleted, cur = [], [], None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            cur = line[4:].strip()
            cur = cur[2:] if cur.startswith("b/") else cur
        if line.startswith("+") and not line.startswith("+++") and _SKIP_RE.search(line):
            skips.append({"file": cur or "?", "text": line[1:].strip()[:120]})
    # 삭제된 테스트 파일: 본선에는 있는데 브랜치에 없는 것
    base = git.merge_base(root, b)
    out = git.run(root, "diff", "--name-status", f"{base}..{b}").stdout
    for l in out.splitlines():
        parts = l.split("\t")
        if len(parts) >= 2 and parts[0].startswith("D") and is_test(parts[1], cfg):
            deleted.append(parts[1])
    return {"skips": skips, "deleted_tests": deleted}


def test_provenance(root, cfg, role):
    """커밋 리듬으로 본 작성 순서 — 증거지 증명은 아니다."""
    b = git.branch(role)
    if not git.branch_exists(root, b):
        return {"verdict": "none", "test_only": 0, "impl_only": 0, "mixed": 0, "commits": []}
    test_only = impl_only = mixed = 0
    commits = []
    for sha, subject, files in git.log(root, b):
        code = [f for f in files if os.path.splitext(f)[1].lower() in CODE_EXT
                and not f.startswith(util.GEN_DIR + "/")]
        if not code:
            continue
        t = [f for f in code if is_test(f, cfg)]
        kind = "test" if len(t) == len(code) else ("impl" if not t else "mixed")
        if kind == "test":
            test_only += 1
        elif kind == "impl":
            impl_only += 1
        else:
            mixed += 1
        commits.append({"sha": sha, "subject": subject[:80], "kind": kind})
    kinds = [c["kind"] for c in commits]
    test_idx = [i for i, k in enumerate(kinds) if k in ("test", "mixed")]
    impl_idx = [i for i, k in enumerate(kinds) if k == "impl"]
    if not test_idx:
        verdict = "none"
    elif not impl_idx or test_idx[0] < impl_idx[0]:
        verdict = "test-first"                     # 첫 테스트 커밋이 첫 구현 커밋보다 앞선다
    elif test_idx[0] > impl_idx[-1]:
        verdict = "test-after"                     # 테스트가 전부 구현 뒤에 붙었다
    else:
        verdict = "mixed"
    return {"verdict": verdict, "test_only": test_only, "impl_only": impl_only, "mixed": mixed,
            "commits": commits}


def run_verify(root, cfg, role):
    """설정된 빌드·테스트 명령을 워크트리에서 재실행한다. 없으면 None.

    신뢰 경계: .handoff/config.json 이 git 에 추적돼 있으면 실행하지 않는다 — 레포가 심어 놓은
    명령이 남의 기기에서 도는 것을 막는다.
    """
    cmds = (cfg.get("verify", {}).get("commands") or {}).get(role) or []
    if not cmds:
        return None
    tracked = git.run(root, "ls-files", "--", f"{util.HO_DIR}/{util.CONFIG}", check=False).stdout.strip()
    if tracked:
        return {"ran": 0, "passed": 0, "seconds": 0, "refused": "config.json 이 git 에 추적돼 있어 실행하지 않았다"}
    wt = util.worktree(root, role)
    cwd = os.path.join(wt, cfg["roles"].get(role, role))
    if not os.path.isdir(cwd):
        cwd = wt
    results, passed, t0 = [], 0, time.time()
    for c in cmds:
        try:
            r = subprocess.run(c, shell=True, cwd=cwd, capture_output=True, text=True,
                               timeout=cfg["verify"].get("timeout_sec", 600))
            ok = r.returncode == 0
            tail = leaks.mask((r.stdout + r.stderr)[-1500:])
        except subprocess.TimeoutExpired:
            ok, tail = False, "timeout"
        except OSError as e:
            ok, tail = False, str(e)
        passed += ok
        results.append({"cmd": c, "ok": ok, "tail": tail})
    return {"ran": len(cmds), "passed": passed, "seconds": round(time.time() - t0, 1), "results": results}


# ─────────────────────────── 시크릿 ───────────────────────────

def secret_hits(tree, role_path, cfg):
    out = []
    for rel, text in iter_code(tree, role_path, cfg):
        if leaks.EXAMPLE_RE.search(rel):
            continue
        for ln, line in enumerate(text.splitlines(), 1):
            for rule, _ in leaks.find(line):
                out.append(f"{rel}:{ln} [{rule}]")
                break
    return out


def history_leaks(root, role):
    """브랜치 커밋 이력의 diff 에서 시크릿 — 넣었다 지운 키도 잡는다."""
    b = git.branch(role)
    if not git.branch_exists(root, b):
        return []
    base = git.merge_base(root, b)
    out = git.run(root, "log", "-p", "--format=%x1e%h", f"{base}..{b}", check=False).stdout
    hits, sha, file = [], "?", "?"
    for line in out.split("\n"):
        if line.startswith("\x1e"):
            sha = line[1:].strip()
        elif line.startswith("+++ "):
            file = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("+") and not line.startswith("+++"):
            if leaks.EXAMPLE_RE.search(file):
                continue
            for rule, _ in leaks.find(line):
                hits.append(f"{file} [{rule}] @{sha}")
                break
    return sorted(set(hits))
