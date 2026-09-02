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
import base64
import gzip
import json
import os
import re
import shutil
import tarfile
import zipfile

from . import util

SKIP_DIRS = {"assets", "asset", "images", "img", "fonts", "node_modules", "__MACOSX", "js", "styles", "uploads", "screenshots"}
MANIFEST_NAME = "handoff.manifest.json"       # 사람이 확정한 화면 목록 — design/ 안에 살아 지문에 든다
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
HTML_EXT = (".html", ".htm")

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_SECTION_RE = re.compile(
    r"<(section|div|article)\b[^>]*\b(?:data-screen|data-artboard|id)\s*=\s*[\"']([^\"']+)[\"'][^>]*>", re.I)
_CSS_VAR_RE = re.compile(r"--([A-Za-z][\w-]*)\s*:\s*([^;}]+)")
_STATE_RE = re.compile(r"<sc-if\s+value=\"\{\{\s*(is[A-Z]\w*)\s*\}\}\"")
_CANVAS_RE = re.compile(r'<meta\s+name="design_doc_mode"\s+content="canvas"', re.I)
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
    elif is_bundled_html(src):
        os.makedirs(tmp, exist_ok=True)
        unbundle(src, tmp)
    else:
        raise DesignError(f"zip · tar.gz · 번들 html · 디렉토리 중 어느 것도 아니다: {src}")
    _collapse_single_dir(tmp)
    # 폴더 안에 "standalone HTML" 내보내기가 섞여 있으면 그것도 펼친다
    for rel in list(_all_files(tmp)):
        fp = os.path.join(tmp, rel)
        if rel.lower().endswith((".html", ".htm")) and is_bundled_html(fp):
            unbundle(fp, os.path.dirname(fp))
            os.remove(fp)
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


_BUNDLE_MARK = '<script type="__bundler/manifest"'
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SKIP_EXT = ("unpkg.com/react", "unpkg.com/react-dom", "@babel/standalone")


def is_bundled_html(path):
    """Claude Design 의 'standalone HTML' 내보내기인가 — 자산이 gzip+base64 매니페스트로 인라인된 한 파일."""
    try:
        with open(path, "rb") as f:
            head = f.read(200_000).decode("utf-8", "replace")
    except OSError:
        return False
    return _BUNDLE_MARK in head


def _bundle_block(html, kind):
    m = re.search(r'<script type="__bundler/%s"[^>]*>(.*?)</script>' % kind, html, re.S)
    return json.loads(m.group(1)) if m else None


def unbundle(path, dest):
    """번들 html → 아트보드(.dc.html) + 그 안의 파일들(jsx · 런타임 · 폰트 · 이미지)로 펼친다.

    매니페스트는 uuid → {mime, compressed, data}. 템플릿(아트보드 본문)은 자산을 uuid 로 가리키고,
    이름이 살아 있는 것은 `uuid#/경로` 꼴이다. 이름이 없는 것은 mime 으로 폴더를 정한다.
    CDN 의존성(react · babel)은 디자인이 아니라 뺀다.
    """
    html = util.read_text(path)
    man = _bundle_block(html, "manifest") or {}
    tpl = _bundle_block(html, "template") or ""
    ext = {e["uuid"]: e["id"] for e in (_bundle_block(html, "ext_resources") or []) if isinstance(e, dict)}
    names = {m.group(1): m.group(2) for m in re.finditer(r"(%s)#/([^\"'\s>]+)" % _UUID_RE.pattern, tpl)}
    written = {}
    for uid, e in man.items():
        if not isinstance(e, dict) or "data" not in e:
            continue
        if uid in ext and any(k in ext[uid] for k in _SKIP_EXT):
            continue
        try:
            raw = base64.b64decode(e["data"])
            data = gzip.decompress(raw) if e.get("compressed") else raw
        except Exception:
            continue
        mime = str(e.get("mime") or "")
        head = data[:80].decode("utf-8", "replace")
        if uid in names:
            rel = names[uid]
        elif "GENERATED from dc-runtime" in head:
            rel = "support.js"
        elif head.startswith("/* @ds-bundle"):
            rel = "_ds/_ds_bundle.js"
        elif mime.startswith("font/"):
            rel = f"fonts/{uid[:8]}.{mime.split('/')[-1]}"
        elif mime.startswith("image/"):
            rel = f"uploads/{uid[:8]}.{ {'jpeg': 'jpg', 'svg+xml': 'svg'}.get(mime.split('/')[-1], mime.split('/')[-1]) }"
        elif mime.endswith("javascript") or mime == "text/jsx":
            rel = f"js/{uid[:8]}.{'jsx' if mime == 'text/jsx' else 'js'}"
        elif mime == "text/css":
            rel = f"styles/{uid[:8]}.css"
        else:
            rel = f"assets/{uid[:8]}"
        out = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(data)
        written[uid] = rel
    # 템플릿의 uuid 참조를 되돌린다
    for uid, rel in names.items():
        tpl = tpl.replace(f"{uid}#/{rel}", "./" + rel)
    for uid, rel in written.items():
        tpl = tpl.replace(uid, "./" + rel)
    stem = stem_of(path)
    util.write_text(os.path.join(dest, f"{stem}.dc.html"), tpl)
    return written


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


def is_board(html):
    """탐색 보드(여러 안을 나란히 놓은 캔버스)인가 — 화면이 아니라 참고 자료다."""
    return bool(_CANVAS_RE.search(html)) or html.count("<x-import ") >= 3


def state_screens(html):
    """프로토타입 한 파일 안의 화면 후보 — <sc-if value="{{ isHome }}"> 같은 상태 분기. 순서 유지, 중복 제거."""
    out = []
    for name in _STATE_RE.findall(html):
        sid = name[2:]
        sid = sid[:1].lower() + sid[1:]
        if sid not in out:
            out.append(sid)
    return out


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
                    screens.append({"id": slug(str(name)), "file": f, "anchor": s.get("anchor"),
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
    htmls, boards = [], []
    for f in files:
        if not f.lower().endswith(HTML_EXT) or re.search(r"[-_. ](print|preview)$", stem_of(f), re.I):
            continue                                                            # 인쇄용 변형은 화면이 아니다
        (boards if is_board(util.read_text(os.path.join(d, f))) else htmls).append(f)
    mds = [f for f in everything if f.lower().endswith(".md")]
    chats = sorted(f for f in everything if is_chat(f) and f.lower().endswith((".md", ".json", ".txt")))
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
            screens.append({"id": s["id"], "file": f, "title": s["title"], "anchor": s.get("anchor")})
    else:
        for h in htmls:
            stem = stem_of(h)
            if stem.lower() == "index" and len(htmls) > 1:
                continue
            html = util.read_text(os.path.join(d, h))
            states = state_screens(html) if h.lower().endswith(".dc.html") else []
            sections = [] if len(htmls) > 1 else [
                (kind, sid) for kind, sid in _SECTION_RE.findall(html)
                if not sid.lower().startswith(("nav", "toc", "menu", "root", "app", "wrap"))]
            if len(states) >= 2:
                # 프로토타입 하나에 화면 여럿 — 상태 분기가 화면이다. 사람이 목록을 확정한다 (handoff.manifest.json)
                for st in states:
                    screens.append({"id": st if len(htmls) == 1 else slug(f"{stem} {st}"), "file": h,
                                    "title": st, "anchor": st, "state": True})
            elif len(sections) >= 2:
                seen = set()
                for _, sid in sections:
                    sid_slug = slug(sid)
                    if sid_slug not in seen:
                        seen.add(sid_slug)
                        screens.append({"id": sid_slug, "file": h, "title": sid, "anchor": sid})
            else:
                title = _title_of(html, stem)
                screens.append({"id": slug(stem), "file": h, "title": stem if "{{" in title else title, "anchor": None})
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

    used = set(htmls) | set(boards) | set(docs) | set(chats) | set(images)
    assets = [f for f in everything if f not in used]
    return {"hash": util.tree_hash(d), "screens": screens, "tokens": tok, "boards": boards,
            "docs": docs, "chats": chats, "assets": assets, "files": len(everything),
            "confirmed": os.path.isfile(os.path.join(d, MANIFEST_NAME))}


def confirm_screens(root, screens):
    """사람이 확정한 화면 목록을 design/handoff.manifest.json 으로 쓴다. [{id, title, file?, anchor?}]"""
    d = util.design_dir(root)
    if not os.path.isdir(d):
        raise DesignError("design/ 가 없다")
    rows = []
    for s in screens or []:
        if isinstance(s, str):
            rows.append({"id": slug(s), "title": s})
        elif isinstance(s, dict) and (s.get("id") or s.get("title")):
            rows.append({"id": slug(str(s.get("id") or s["title"])), "title": str(s.get("title") or s["id"]),
                         "file": s.get("file"), "anchor": s.get("anchor")})
    if not rows:
        raise DesignError("화면 목록이 비었다")
    util.write_json(os.path.join(d, MANIFEST_NAME), {"screens": rows})
    return rows


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
