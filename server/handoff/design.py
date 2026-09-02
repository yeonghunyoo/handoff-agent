"""핸드오프 패키지 인제스트 — design/ 를 읽어 화면 · 토큰 · 문서 · 스크린샷을 발견한다.

패키지 포맷은 고정돼 있지 않다(Claude Design 내보내기는 HTML 화면 · 상태별 스크린샷 · README ·
기계용 스펙/토큰이 든 zip 이라는 것만 알려져 있다). 그래서 발견 규칙으로 읽고, 규칙은 관대하다:

  1. 최상위 json 에 screens/tokens 가 있으면 그것을 믿는다 (패키지 자체의 매니페스트)
  2. 화면 = html 파일 하나. html 이 하나뿐이고 안에 섹션(data-screen / <section id>)이 여럿이면 그 섹션들
  3. 토큰 = css/html 의 --커스텀-프로퍼티 + *token*.json (W3C $value 형식 포함)
  4. 문서 = *.md (README 먼저) · 스크린샷 = 화면 이름과 맞는 이미지 · 나머지는 자산

매니페스트는 저장하지 않는다 — design/ 에서 매번 같은 답이 나온다(결정적). 잠금은 design/ 의
트리 해시로 잡는다.
"""
import json
import os
import re
import shutil
import tarfile
import zipfile

from . import util

SKIP_DIRS = {"assets", "asset", "images", "img", "fonts", "node_modules", "__MACOSX"}
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
HTML_EXT = (".html", ".htm")

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_SECTION_RE = re.compile(
    r"<(section|div|article)\b[^>]*\b(?:data-screen|data-artboard|id)\s*=\s*[\"']([^\"']+)[\"'][^>]*>", re.I)
_CSS_VAR_RE = re.compile(r"--([A-Za-z][\w-]*)\s*:\s*([^;}]+)")
_COLOR_RE = re.compile(r"^(#[0-9a-fA-F]{3,8}|(rgba?|hsla?|oklch|oklab|color)\(.*\))$")
_DIM_RE = re.compile(r"^(-?\d+(?:\.\d+)?)(px|pt|dp|sp|rem|em)?$")


class DesignError(ValueError):
    pass


# ─────────────────────────── 가져오기 ───────────────────────────

def import_package(root, src):
    """zip 또는 디렉토리를 design/ 로 옮긴다 (기존 design/ 은 교체). 반환: 원본 이름."""
    src = os.path.abspath(os.path.expanduser(src))
    if not os.path.exists(src):
        raise DesignError(f"경로가 없다: {src}")
    dest = util.design_dir(root)
    if os.path.abspath(dest) == src:
        return os.path.basename(src)          # 이미 design/ 에 있다 — 그대로 읽는다
    tmp = dest + ".importing"
    shutil.rmtree(tmp, ignore_errors=True)
    if os.path.isdir(src):
        shutil.copytree(src, tmp, ignore=shutil.ignore_patterns(".git", ".DS_Store", "__MACOSX"))
    elif zipfile.is_zipfile(src):
        _unzip(src, tmp)
    elif tarfile.is_tarfile(src):
        _untar(src, tmp)
    else:
        raise DesignError(f"zip · tar.gz · 디렉토리 중 어느 것도 아니다: {src}")
    _collapse_single_dir(tmp)
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)
    return os.path.basename(src)


def _unzip(path, dest):
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            name = info.filename
            if name.startswith("__MACOSX/") or os.path.basename(name) == ".DS_Store":
                continue
            target = os.path.normpath(os.path.join(dest, name))
            if not target.startswith(os.path.abspath(dest) + os.sep) and target != os.path.abspath(dest):
                continue                       # zip slip
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as s, open(target, "wb") as d:
                shutil.copyfileobj(s, d)


def _untar(path, dest):
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(path) as t:
        for m in t.getmembers():
            name = m.name
            if name.startswith("__MACOSX/") or os.path.basename(name) == ".DS_Store" or not (m.isfile() or m.isdir()):
                continue
            target = os.path.normpath(os.path.join(dest, name))
            if not target.startswith(os.path.abspath(dest) + os.sep):
                continue
            if m.isdir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            src = t.extractfile(m)
            if src is None:
                continue
            with src, open(target, "wb") as d:
                shutil.copyfileobj(src, d)


def _collapse_single_dir(d):
    """zip 이 폴더 하나로 감싸져 있으면 한 겹 벗긴다."""
    entries = [e for e in os.listdir(d) if not e.startswith(".")]
    if len(entries) == 1 and os.path.isdir(os.path.join(d, entries[0])):
        inner = os.path.join(d, entries[0])
        for e in os.listdir(inner):
            shutil.move(os.path.join(inner, e), os.path.join(d, e))
        os.rmdir(inner)


# ─────────────────────────── 발견 ───────────────────────────

def _walk(d):
    for base, dirs, names in os.walk(d):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and not x.startswith("_")
                         and x.lower() not in SKIP_DIRS)
        for n in sorted(names):
            if n.startswith("."):
                continue
            yield os.path.relpath(os.path.join(base, n), d).replace(os.sep, "/")


def _all_files(d):
    for base, dirs, names in os.walk(d):
        dirs[:] = sorted(x for x in dirs if x != "__MACOSX")
        for n in sorted(names):
            if n != ".DS_Store":
                yield os.path.relpath(os.path.join(base, n), d).replace(os.sep, "/")


def slug(name):
    """파일 이름 → lowerCamel 식별자. '01-order-list' → orderList, 'Order List' → orderList."""
    stem = re.sub(r"^\d+[-_. ]+", "", name)
    parts = [p for p in re.split(r"[^0-9A-Za-z]+", stem) if p]
    if not parts:
        return "screen"
    words = []
    for p in parts:
        # 이미 camelCase 인 조각은 쪼갠다
        words += re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", p)
    out = words[0].lower() + "".join(w[:1].upper() + w[1:].lower() for w in words[1:])
    if out[0].isdigit():
        out = "s" + out
    return out


def stem_of(path):
    """파일 이름 → 화면 이름 줄기. 'Order List.dc.html' → 'Order List' (.dc 는 Design Components 확장)."""
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem[:-3] if stem.lower().endswith(".dc") else stem


def is_chat(rel):
    parts = rel.lower().replace("\\", "/").split("/")
    return any(p in ("chat", "chats", "transcript", "transcripts", "conversation", "conversations") for p in parts[:-1]) \
        or any(k in parts[-1] for k in ("chat", "transcript", "conversation"))


def _text(html_fragment):
    return re.sub(r"\s+", " ", _TAG_RE.sub("", html_fragment)).strip()


def _title_of(html, fallback):
    m = _TITLE_RE.search(html) or _H1_RE.search(html)
    t = _text(m.group(1)) if m else ""
    return t or fallback


def _manifest_override(d):
    """패키지가 스스로 매니페스트를 갖고 있으면 (최상위 json 에 screens/tokens) 그것을 쓴다."""
    screens, tokens = None, {}
    for n in sorted(os.listdir(d)):
        if not n.endswith(".json"):
            continue
        obj = util.read_json(os.path.join(d, n))
        if not isinstance(obj, dict):
            continue
        sc = obj.get("screens")
        if isinstance(sc, list) and sc and screens is None:
            screens = []
            for s in sc:
                if isinstance(s, str):
                    screens.append({"id": slug(s), "file": None, "title": s})
                elif isinstance(s, dict):
                    f = s.get("file") or s.get("path") or s.get("html")
                    name = s.get("id") or s.get("name") or (os.path.splitext(os.path.basename(f))[0] if f else None)
                    if not name:
                        continue
                    screens.append({"id": slug(str(name)), "file": f,
                                    "title": str(s.get("title") or s.get("name") or name)})
        tk = obj.get("tokens")
        if isinstance(tk, dict):
            tokens.update(_flatten_tokens(tk))
    return screens, tokens


def _flatten_tokens(obj, prefix=""):
    """중첩 json → 점 경로. {"$value": ..} / {"value": ..} 는 잎으로 본다."""
    out = {}
    if isinstance(obj, dict):
        leaf = obj.get("$value", obj.get("value")) if ("$value" in obj or "value" in obj) else None
        if leaf is not None and not isinstance(leaf, dict):
            out[prefix] = leaf
            return out
        for k, v in obj.items():
            if str(k).startswith("$"):
                continue
            key = f"{prefix}.{k}" if prefix else str(k)
            out.update(_flatten_tokens(v, key))
    elif prefix and isinstance(obj, (str, int, float)):
        out[prefix] = obj
    return out


def classify(value):
    """토큰 값 → (kind, 정규화 값). color | dimension | other."""
    s = str(value).strip()
    if _COLOR_RE.match(s):
        return "color", s
    m = _DIM_RE.match(s)
    if m:
        num = float(m.group(1))
        if m.group(2) in ("rem", "em"):
            num *= 16
        return "dimension", int(num) if num.is_integer() else num
    return "other", s


def _css_tokens(text):
    out = {}
    for name, val in _CSS_VAR_RE.findall(text):
        val = val.strip()
        if val.startswith("var("):
            continue
        out[name.replace("-", ".")] = val
    return out


def scan(root):
    """design/ → 매니페스트 dict. 화면이 없으면 DesignError."""
    d = util.design_dir(root)
    if not os.path.isdir(d):
        raise DesignError("design/ 가 없다 — 핸드오프 패키지를 먼저 가져온다")
    files = list(_walk(d))
    everything = list(_all_files(d))
    htmls = [f for f in files if f.lower().endswith(HTML_EXT)
             and not re.search(r"[-_. ](print|preview)$", stem_of(f), re.I)]   # 인쇄용 변형은 화면이 아니다
    mds = [f for f in everything if f.lower().endswith(".md")]
    chats = sorted(f for f in mds if is_chat(f))
    docs = sorted((f for f in mds if f not in chats),
                  key=lambda f: (0 if os.path.basename(f).lower().startswith("readme") else 1, f))
    images = [f for f in everything if f.lower().endswith(IMG_EXT)]

    # 화면
    override, tokens = _manifest_override(d)
    screens = []
    if override:
        for s in override:
            f = s["file"]
            if f and f not in htmls:
                cand = [h for h in htmls if os.path.basename(h) == os.path.basename(f)]
                f = cand[0] if cand else None
            if not f:
                cand = [h for h in htmls if slug(stem_of(h)) == s["id"]]
                f = cand[0] if cand else None
            screens.append({"id": s["id"], "file": f, "title": s["title"], "anchor": None})
    elif len(htmls) == 1:
        html = util.read_text(os.path.join(d, htmls[0]))
        sections = [(kind, sid) for kind, sid in _SECTION_RE.findall(html)
                    if not sid.lower().startswith(("nav", "toc", "menu", "root", "app", "wrap"))]
        seen = set()
        for _, sid in sections:
            sid_slug = slug(sid)
            if sid_slug in seen:
                continue
            seen.add(sid_slug)
            screens.append({"id": sid_slug, "file": htmls[0], "title": sid, "anchor": sid})
        if len(screens) < 2:
            stem = stem_of(htmls[0])
            screens = [{"id": slug(stem), "file": htmls[0], "title": _title_of(html, stem), "anchor": None}]
    else:
        for h in htmls:
            stem = stem_of(h)
            if stem.lower() == "index" and len(htmls) > 1:
                continue
            html = util.read_text(os.path.join(d, h))
            screens.append({"id": slug(stem), "file": h, "title": _title_of(html, stem), "anchor": None})
    if not screens:
        raise DesignError("화면(html)을 하나도 찾지 못했다 — 패키지에 화면 html 이 있어야 한다")
    ids = [s["id"] for s in screens]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise DesignError("화면 id 가 겹친다 (파일 이름을 다르게): " + ", ".join(dup))

    # 토큰 — json 매니페스트 < *token*.json < css 변수 순으로 덮는다 (css 가 실제 칠해진 값)
    for f in everything:
        base = os.path.basename(f).lower()
        if base.endswith(".json") and "token" in base:
            obj = util.read_json(os.path.join(d, f))
            if isinstance(obj, dict):
                tokens.update(_flatten_tokens(obj.get("tokens", obj)))
    for f in everything:
        if f.lower().endswith((".css", ".html", ".htm")):
            tokens.update(_css_tokens(util.read_text(os.path.join(d, f))))
    tok = {}
    for k, v in tokens.items():
        kind, norm = classify(v)
        tok[k] = {"kind": kind, "value": norm}

    # 스크린샷 — 이름이 화면과 맞는 것만
    shots = {s["id"]: [] for s in screens}
    by_stem = {}
    for s in screens:
        by_stem[s["id"]] = s["id"]
        if s["file"]:
            by_stem[slug(stem_of(s["file"]))] = s["id"]
    for im in images:
        stem = os.path.splitext(os.path.basename(im))[0]
        key = slug(stem.split("__")[0].split("@")[0])
        if key in by_stem:
            shots[by_stem[key]].append(im)
    for s in screens:
        s["shots"] = shots[s["id"]]

    used = set(htmls) | set(docs) | set(chats) | set(images)
    assets = [f for f in everything if f not in used]
    return {"hash": util.tree_hash(d), "screens": screens, "tokens": tok,
            "docs": docs, "chats": chats, "assets": assets, "files": len(everything)}


def manifest(root):
    try:
        return scan(root)
    except DesignError:
        return None


def readme_hints(root, m=None):
    """README 에서 스택 힌트 문장을 뽑는다 — 인터뷰의 기본값 제안일 뿐, 사람이 확정한다."""
    m = m or manifest(root)
    if not m or not m["docs"]:
        return []
    text = util.read_text(os.path.join(util.design_dir(root), m["docs"][0]))
    hints = []
    for line in text.splitlines():
        if re.search(r"(?i)\b(stack|framework|backend|swiftui|compose|kotlin|swift|react|next|"
                     r"fastapi|django|nest|express|supabase|firebase|postgres|auth)\b", line):
            hints.append(line.strip()[:200])
    return hints[:12]
