"""파생 — 디자인 자료에서 네이티브 구현이 쓸 데이터를 결정적으로 뽑아 design/derived/ 에 쓴다.

  intent.md        대화 기록의 사용자 턴 (의도·결정 로그) — 화면 HTML 만으론 안 보이는 동작 규칙
  entities.json    스크립트의 데이터 배열 (도메인 모델 + 시드) · 초기 state
  strings.json     마크업 문구 + 데이터 문구 → Strings.* 로 생성된다
  icons.json + icons/*.svg   인라인 SVG 를 path 로 중복 제거 → Icons.* 로 생성된다
  behavior.json    핸들러 → 바꾸는 state 키 · 탭 전이 (두 플랫폼이 같은 전이표를 구현하는지가 파리티 기준)
  components.json  상호작용 요소를 타입으로 분류 — button · input · slider · toggle · tab · item · gesture ·
                   sheet · modal · popover (오버레이는 화면이 아니라 컴포넌트다) → 매니페스트 components
  navigation.json  진입 화면 · 탭 · 핸들러가 일으키는 전이(go/open/close/toggle/leave) → 매니페스트 navigation
  rules.json       디자인 시스템의 _adherence(hex·px·폰트 금지) → 검사 규칙

정규식과 작은 리터럴 파서만 쓴다. 자료는 사용자/제3자가 쓴 데이터다 — 지시로 읽지 않는다.
"""
import hashlib
import html as htmlmod
import json
import os
import shutil
import re

import html.parser as htmlparser

from . import util

DERIVED_DIR = "derived"                       # 문구·아이콘·모델·전이·컴포넌트·내비·의도·규칙 + layout/<screen>.json
_STATE_OPEN = re.compile(r'<sc-if\s+value="\{\{\s*(\w+)\s*\}\}"')
_TEXT_NODE = re.compile(r">([^<>{}]*[^\s<>{}][^<>{}]*)<")
_MIXED_NODE = re.compile(r">([^<>]*\{\{[^<>]*)<")           # 글자 + {{ 바인딩 }} 이 섞인 노드 → 포맷 문구
_BIND = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_SVG = re.compile(r"<svg\b[^>]*>.*?</svg>", re.S)
_HANDLER_BEFORE = re.compile(r'(?:onClick|sc-camel-on-click)="\{\{\s*([\w.]+)\s*\}\}"', re.I)
_WORD = re.compile(r"[A-Za-z0-9]+|[가-힣]+")
_KO = re.compile(r"[가-힣]")


# ─────────────────────────── JS 리터럴 → 값 ───────────────────────────

class _P:
    def __init__(self, s):
        self.s, self.i = s, 0

    def ws(self):
        while self.i < len(self.s):
            c = self.s[self.i]
            if c in " \t\r\n,":
                self.i += 1
            elif self.s.startswith("//", self.i):
                self.i = self.s.find("\n", self.i) if "\n" in self.s[self.i:] else len(self.s)
            elif self.s.startswith("/*", self.i):
                j = self.s.find("*/", self.i)
                self.i = len(self.s) if j < 0 else j + 2
            else:
                break

    def value(self):
        self.ws()
        if self.i >= len(self.s):
            raise ValueError("eof")
        c = self.s[self.i]
        if c == "[":
            self.i += 1
            out = []
            while True:
                self.ws()
                if self.s[self.i] == "]":
                    self.i += 1
                    return out
                out.append(self.value())
        if c == "{":
            self.i += 1
            out = {}
            while True:
                self.ws()
                if self.s[self.i] == "}":
                    self.i += 1
                    return out
                key = self.key()
                self.ws()
                if self.s[self.i] != ":":
                    raise ValueError("expected :")
                self.i += 1
                out[key] = self.value()
        if c in "'\"`":
            return self.string(c)
        m = re.match(r"-?\d+(?:\.\d+)?", self.s[self.i:])
        if m:
            self.i += m.end()
            return float(m.group()) if "." in m.group() else int(m.group())
        m = re.match(r"true|false|null|undefined", self.s[self.i:])
        if m:
            self.i += m.end()
            return {"true": True, "false": False}.get(m.group())
        # 표현식(함수·참조) — 원문을 문자열로 보존하고 다음 , 나 닫는 괄호까지 건너뛴다
        depth, j = 0, self.i
        while j < len(self.s):
            ch = self.s[j]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif ch == "," and depth == 0:
                break
            j += 1
        raw = self.s[self.i:j].strip()
        self.i = j
        return {"$expr": raw}

    def key(self):
        c = self.s[self.i]
        if c in "'\"":
            return self.string(c)
        m = re.match(r"[A-Za-z_$][\w$]*", self.s[self.i:])
        if not m:
            raise ValueError("bad key")
        self.i += m.end()
        return m.group()

    def string(self, q):
        self.i += 1
        out = []
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\" and self.i + 1 < len(self.s):
                out.append(self.s[self.i + 1])
                self.i += 2
                continue
            if c == q:
                self.i += 1
                return "".join(out)
            out.append(c)
            self.i += 1
        raise ValueError("unterminated string")


def js_literal(text):
    return _P(text).value()


def _script_of(html):
    m = re.search(r"<script[^>]*data-dc-script[^>]*>(.*?)</script>", html, re.S)
    return m.group(1) if m else ""


def _markup_of(html):
    return re.split(r"<script[^>]*data-dc-script", html, 1)[0]


# ─────────────────────────── 슬러그 ───────────────────────────

_CHO = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
_JUNG = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i"]
_JONG = ["", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "p", "l", "m", "p", "l", "t", "t", "ng",
         "t", "t", "k", "t", "p", "t"]


def romanize(word):
    out = []
    for ch in word:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out.append(_CHO[code // 588] + _JUNG[(code % 588) // 28] + _JONG[code % 28])
        else:
            out.append(ch)
    return "".join(out)


def slug(text, limit=6):
    words = _WORD.findall(str(text))
    out = []
    for w in words[:limit]:
        out.append(romanize(w) if _KO.search(w) else w)
    s = "".join(w[:1].upper() + w[1:] for w in out) or "t" + hashlib.sha1(str(text).encode()).hexdigest()[:6]
    s = s[:1].lower() + s[1:]
    return "_" + s if s[0].isdigit() else s


# ─────────────────────────── entities · behavior ───────────────────────────

def entities(html):
    """const NAME = [...] / {...} 와 초기 state."""
    script = _script_of(html)
    out = {}
    for m in re.finditer(r"^\s*const\s+([A-Z][A-Z0-9_]*)\s*=\s*(?=[\[{])", script, re.M):
        try:
            out[m.group(1)] = js_literal(script[m.end():])
        except ValueError:
            continue
    m = re.search(r"\bstate\s*=\s*(?=\{)", script)
    if m:
        try:
            out["_state"] = js_literal(script[m.end():])
        except ValueError:
            pass
    return out


def _handler_bodies(script):
    """[(name, body)] — 클래스 필드 화살표 핸들러 `  name = (…) => …` 를 다음 핸들러/renderVals 전까지."""
    return [(m.group(1), m.group(2)) for m in re.finditer(
        r"^\s{2}(\w+)\s*=\s*\([^)]*\)\s*=>\s*(.*?)(?=^\s{2}\w+\s*=\s*\(|^\s{2}renderVals\(|\Z)", script, re.S | re.M)]


def behavior(html):
    script = _script_of(html)
    handlers = {}
    for name, body in _handler_bodies(script):
        sets = sorted(set(re.findall(r"\b([a-z]\w*)\s*:", body)) - {"e", "s", "st"})
        calls = sorted(set(re.findall(r"this\.(\w+)\(", body)) - {"setState"})
        handlers[name] = {"sets": sets, "calls": calls}
    tabs = {}
    for name, target in re.findall(r"(\w+):\s*\(\)\s*=>\s*this\.setTab\('(\w+)'\)", script):
        tabs[name] = {"tab": target}
    for name, key, val in re.findall(r"(\w+):\s*\(\)\s*=>\s*this\.setState\(\{\s*(\w+):\s*'(\w+)'", script):
        tabs.setdefault(name, {key: val})
    timers = sorted(set(re.findall(r"setTimeout\([^,]+,\s*(\d+)\)", script)) | set(re.findall(r"setInterval\([^,]+,\s*(\d+)\)", script)))
    return {"handlers": handlers, "tab_transitions": tabs, "timers_ms": [int(t) for t in timers]}


# ─────────────────────────── strings ───────────────────────────

_TAG = re.compile(r"<(/?)([a-zA-Z][\w-]*)([^>]*?)(/?)>", re.S)
_VOID = {"br", "img", "input", "hr", "meta", "link", "path", "circle", "rect", "line", "polyline", "polygon", "use"}
_STYLE_BIND = re.compile(r'style="\{\{\s*(\w+)\s*\}\}')


def _overlay_ids(screens, comps=()):
    """style="{{ nowPlayingStyle }}" 같은 블록 → 화면/컴포넌트 id. 컴포넌트는 스타일 변수 이름으로, 화면은
    anchor 'nowPlayingOpen' 의 줄기 'nowplaying' 로 맞춘다. 같은 블록을 사람이 화면으로 승격했으면 화면이 이긴다."""
    out = {}
    for c in comps:
        if c.get("style"):
            out[c["style"].lower()] = c["id"]
    for s in screens:
        a = (s.get("anchor") or "")
        if a and not a.startswith("is"):
            out[re.sub(r"(open|visible|shown)$", "", a.lower())] = s["id"]
    return out


def _screen_for(screens, key):
    """'isHome' · 'home' → 화면 id (id 또는 anchor 가 맞는 것)."""
    k = key[2:3].lower() + key[3:] if re.match(r"is[A-Z]", key) else key
    return next((s["id"] for s in screens if s["id"] == k or (s.get("anchor") or "") in (key, k)), None)


_FLAG_LINE = re.compile(r"^\s*(\w+):\s*(.+?),?\s*$", re.M)


def _flag_screens(script, screens):
    """renderVals 의 파생 플래그 → 화면. `showPresetList: s.tab === 'sound' && …` 처럼 탭 비교가 든 것만 —
    그 플래그로 열리는 <sc-if> 블록은 그 탭 화면의 일부다."""
    out = {}
    for name, expr in _FLAG_LINE.findall(script):
        if name.startswith("is"):
            continue
        m = re.search(r"\bs\.tab\s*===?\s*'(\w+)'", expr)
        sid = _screen_for(screens, m.group(1)) if m else None
        if sid:
            out[name] = sid
    return out


def regions_of(html, screens, comps=()):
    """html 전체 → (markup, regions). 상태 분기(is*) · 파생 플래그(renderVals 의 탭 비교) · 오버레이 스타일 블록."""
    markup = _markup_of(html)
    return markup, screen_regions(markup, screens, comps, _flag_screens(_script_of(html), screens))


def screen_regions(markup, screens, comps=(), flags=None):
    """[(start, end, screen_id)] — 텍스트/아이콘/컴포넌트의 화면 귀속에 쓴다. 안쪽 구역이 우선한다.
    오버레이 컴포넌트(sheet·modal·popover)도 구역이다 — 그 안의 문구는 오버레이 id 로 귀속된다.
    flags: {파생 플래그: 화면 id} — `<sc-if value="{{ showPresetList }}">` 를 그 화면에 붙인다."""
    state_ids = {s.get("anchor"): s["id"] for s in screens if s.get("anchor")}
    overlays = _overlay_ids(screens, comps)
    regions, stack = [], []
    for m in _TAG.finditer(markup):
        closing, tag, attrs, selfclose = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        if closing:
            for i in range(len(stack) - 1, -1, -1):
                if stack[i][0] == tag:
                    _, start, label = stack.pop(i)
                    if label:
                        regions.append((start, m.end(), label))
                    break
            continue
        if selfclose or tag in _VOID:
            continue
        label = None
        if tag == "sc-if":
            v = re.search(r'value="\{\{\s*(\w+)\s*\}\}"', attrs)
            if v and v.group(1).startswith("is"):
                label = state_ids.get(v.group(1)) or v.group(1)[2:3].lower() + v.group(1)[3:]
            elif v and flags and v.group(1) in flags:
                label = flags[v.group(1)]
        else:
            v = _STYLE_BIND.search(attrs)
            if v:
                low = v.group(1).lower()
                stem = re.sub(r"(sheetstyle|backdropstyle|style)$", "", low)
                label = overlays.get(low) or overlays.get(stem) or overlays.get(stem + "sheet")
        stack.append((tag, m.start(), label))
    return sorted(regions)


def screen_at(regions, pos):
    best = None
    for start, end, label in regions:
        if start <= pos < end and (best is None or (end - start) < (best[1] - best[0])):
            best = (start, end, label)
    return best[2] if best else "shared"


def strings(html, screens, comps=()):
    """[{key, text, screen, source}] — 마크업 텍스트 노드(화면·오버레이 귀속) + 데이터 문구."""
    markup, regions = regions_of(html, screens, comps)
    rows, seen = [], set()
    for m in _TEXT_NODE.finditer(markup):
        text = htmlmod.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
        if not text or len(text) < 2 and not _KO.search(text):
            continue
        if re.fullmatch(r"[\d.:%\-–·\s]+", text):
            continue
        scr = screen_at(regions, m.start())
        if text in seen:
            continue
        seen.add(text)
        rows.append({"key": f"{scr}.{slug(text)}", "text": text, "screen": scr, "source": "markup"})
    for m in _MIXED_NODE.finditer(markup):
        raw = htmlmod.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
        params = []
        def _ph(b):
            name = re.split(r"[^\w]", b.group(1).strip())[-1] or "value"
            name = name[:1].lower() + name[1:]
            params.append(name)
            return "{" + name + "}"
        text = _BIND.sub(_ph, raw)
        lit = re.sub(r"\{\w+\}", "", text).strip()
        if not params or not lit or not (_KO.search(lit) or len(re.sub(r"[^A-Za-z]", "", lit)) >= 2):
            continue                                       # 순수 바인딩이거나 글자가 없다
        if text in seen:
            continue
        seen.add(text)
        scr = screen_at(regions, m.start())
        rows.append({"key": f"{scr}.{slug(lit)}", "text": text, "screen": scr, "source": "format", "params": params})
    ents = entities(html)
    for arr, val in ents.items():
        if arr.startswith("_") or not isinstance(val, list):
            continue
        for i, item in enumerate(val):
            if not isinstance(item, dict):
                continue
            ident = item.get("id") or item.get("key") or str(i)
            for f in ("title", "name", "label", "desc", "description", "subtitle", "hint"):
                v = item.get(f)
                if isinstance(v, str) and v.strip() and v not in seen:
                    seen.add(v)
                    rows.append({"key": f"{arr.lower()}.{slug(ident)}.{f}", "text": v, "screen": "data", "source": arr})
    script = _script_of(html)
    lits = re.findall(r"'((?:[^'\\\n]|\\.)*[가-힣](?:[^'\\\n]|\\.)*)'", script)
    for tl in re.findall(r"`((?:[^`\\]|\\.)*)`", script):
        if _KO.search(tl):
            lits.append(re.sub(r"\$\{(?:[^{}]|\{[^{}]*\})*\}", "{}", tl))
    for v in lits:
        v = v.strip()
        if v and v not in seen and len(v) <= 40 and "{" not in v.replace("{}", ""):
            seen.add(v)
            rows.append({"key": f"script.{slug(v)}", "text": v, "screen": "shared", "source": "script"})
    used = {}
    for r in rows:
        k = r["key"]
        if k in used:
            used[k] += 1
            r["key"] = f"{k}{used[k]}"
        else:
            used[k] = 1
    return rows


def px_match(html_texts, tokens):
    """프로토타입 마크업(<style> 밖)의 px 값과 치수 토큰 값의 일치 — 토큰 소비(TOK)가 가능한지 미리 안다.
    {"dims": n, "dims_used": k, "px": m, "px_matched": j}"""
    dims = {float(v["value"]) for v in tokens.values() if v.get("kind") == "dimension"}
    px = []
    for html in html_texts:
        body = re.sub(r"<style\b.*?</style>", "", html, flags=re.S | re.I)
        px += [float(x) for x in re.findall(r"\b(\d+(?:\.\d+)?)px\b", body)]
    used = {v for v in px if v in dims}
    return {"dims": len(dims), "dims_used": len(used), "px": len(px), "px_matched": sum(1 for v in px if v in dims)}


# ─────────────────────────── icons ───────────────────────────

_HANDLER_NAMES = {
    "setTabHome": "tabHome", "setTabSound": "tabSound", "setTabStats": "tabStats",
    "openMixer": "mixer", "toggleSleepPicker": "sleepTimer", "closeSheet": "chevronDown",
    "closeNowPlaying": "chevronDown", "closeSettings": "chevronDown", "clearMix": "trash", "openSettings": "settings",
}
_HANDLER_PREFIX = (("close", "chevronDown"), ("open", "play"), ("toggleFav", "star"), ("toggle", "check"), ("play", "playPause"))


def icons(html, screens=(), comps=()):
    """[{name, svg, hash, uses, screens}] — path 집합으로 중복 제거. 이름은 근처 핸들러/문구에서, 없으면 해시."""
    markup, regions = regions_of(html, list(screens), comps)
    found = {}
    for m in _SVG.finditer(markup):
        svg = m.group(0)
        paths = "|".join(sorted(re.findall(r'\bd="([^"]+)"', svg) + re.findall(r"<(?:circle|rect)\b[^>]*>", svg)))
        if not paths:
            continue
        h = hashlib.sha1(paths.encode()).hexdigest()[:6]
        before = markup[max(0, m.start() - 400):m.start()]
        hs = _HANDLER_BEFORE.findall(before)
        handler = hs[-1].split(".")[-1] if hs else ""
        after = markup[m.end():].split("<", 1)[0].strip()
        name = _HANDLER_NAMES.get(handler) or next((n for pre, n in _HANDLER_PREFIX if handler.startswith(pre)), "") \
            or (slug(after) if after else "") or f"icon{h}"
        entry = found.setdefault(h, {"name": name, "hash": h, "uses": 0, "screens": [],
                                     "svg": re.sub(r'\sstyle="[^"]*"', "", svg)})
        entry["uses"] += 1
        scr = screen_at(regions, m.start())
        if scr not in entry["screens"]:
            entry["screens"].append(scr)
        if entry["name"].startswith("icon") and not name.startswith("icon"):
            entry["name"] = name
    # 이름 충돌
    used = {}
    for e in found.values():
        n = e["name"]
        if n in used:
            used[n] += 1
            e["name"] = f"{n}{used[n]}"
        else:
            used[n] = 1
    return sorted(found.values(), key=lambda e: e["name"])


# ─────────────────────────── components · navigation ───────────────────────────

COMPONENT_TYPES = ("sheet", "modal", "popover", "tab", "button", "toggle", "input", "slider", "item", "gesture")
_OPEN_KEY = re.compile(r"(open|visible|shown)$", re.I)
_ON_ATTR = re.compile(r'(?:sc-camel-on-([a-z-]+)|on([A-Z]\w*))="\{\{\s*([\w.]+)\s*\}\}"')
_FOR_ATTR = re.compile(r'list="\{\{\s*([\w.]+)\s*\}\}"[^>]*\bas="(\w+)"')
_LIT_SET = re.compile(r"\b(\w+):\s*(true|false|'[\w-]+'|!\s*s\.\w+)")
_OVERLAY_KINDS = ("sheet", "popover", "modal", "dialog")


def _open_stem(key):
    return _OPEN_KEY.sub("", key.lower())


def _match_open_key(base, keys):
    """오버레이 스타일 줄기('settings' · 'sleep') ↔ state 의 *Open 키('settingsOpen' · 'sleepPickerOpen').
    정확히 같은 줄기가 먼저, 없으면 줄기로 시작하는 가장 짧은 키."""
    stems = {_open_stem(k): k for k in keys}
    if base in stems:
        return stems[base]
    hits = sorted((s for s in stems if base and s.startswith(base)), key=len)
    return stems[hits[0]] if hits else None


def overlays(html, state=None):
    """마크업의 오버레이 블록 — style="{{ settingsSheetStyle }}" 바인딩과 state 의 *Open 키로 판별한다.
    [{id, type(sheet|modal|popover), style, anchor}] 등장 순. 이름에 sheet/popover/modal 이 없으면 state 키와
    줄기가 정확히 같을 때만 modal 로 본다 (contentStyle · obBlobStyle 같은 일반 바인딩을 거른다)."""
    markup = _markup_of(html)
    keys = [k for k, v in ((state if state is not None else (entities(html).get("_state") or {})).items())
            if isinstance(v, bool) and _OPEN_KEY.search(k)]
    out, seen = [], set()
    for m in _STYLE_BIND.finditer(markup):
        var = m.group(1)
        low = var.lower()
        if not low.endswith("style") or "backdrop" in low or var in seen:
            continue
        stem = low[:-5]
        kind = next((k for k in _OVERLAY_KINDS if stem.endswith(k)), None)
        if kind:
            anchor = _match_open_key(stem[:-len(kind)] or stem, keys)
            kind = "modal" if kind == "dialog" else kind
        else:
            anchor = _match_open_key(stem, keys)
            if not anchor or _open_stem(anchor) != stem:
                continue
            kind = "modal"
        seen.add(var)
        out.append({"id": var[:-5], "type": kind, "style": var, "anchor": anchor})
    return out


def _inner_end(markup, tag, start):
    """여는 태그 끝(start)부터 같은 태그의 짝 닫힘 위치."""
    depth = 0
    for m in _TAG.finditer(markup, start):
        if m.group(2).lower() != tag:
            continue
        if m.group(1):
            if depth == 0:
                return m.start()
            depth -= 1
        elif not m.group(4):
            depth += 1
    return len(markup)


def _label_of(inner):
    text = re.sub(r"<svg\b.*?</svg>", "", inner, flags=re.S)
    text = htmlmod.unescape(re.sub(r"\s+", " ", _TAG.sub(" ", text))).strip()
    return text[:60]


def _attr(attrs, name):
    m = re.search(r'\b%s="([^"]*)"' % re.escape(name), attrs)
    return htmlmod.unescape(m.group(1)) if m else None


def components(html, screens, confirmed=None):
    """[{id, type, title, screen, …}] — 상호작용 요소를 타입으로 분류한다 (문서 순, 오버레이 먼저).

      sheet · modal · popover   오버레이 블록 (overlays) — open/close 핸들러 · anchor(state 키) · style 변수
      tab                       setTab*/setSubTab* 클릭 — target 전이
      toggle                    toggle* 클릭 (열림 상태를 바꾸지 않는 것) · 반복 항목의 *.toggle
      input · slider            <input> (type=range 는 slider) — bind · placeholder · handler
      item                      반복(sc-for) 안의 클릭 항목 — list 변수 · 항목 핸들러
      gesture                   press/touch 핸들러만 있는 요소
      button                    나머지 클릭 요소 — title 은 안의 문구, 없으면 핸들러 이름 (icon: 아이콘만 있음)

    confirmed: 사람이 확정한 컴포넌트 [{id,type,title,anchor}] — 같은 anchor/id 의 오버레이는 그 이름·타입을 쓴다.
    화면으로 승격된 오버레이(같은 anchor 의 화면이 있음)는 컴포넌트로 세지 않는다."""
    markup = _markup_of(html)
    state = entities(html).get("_state") or {}
    beh = behavior(html)
    bodies = _handler_bodies(_script_of(html))
    screen_anchors = {s.get("anchor") for s in screens if s.get("anchor")}
    ov = [o for o in overlays(html, state) if o["anchor"] not in screen_anchors]
    if confirmed:
        by_id = {c["id"]: c for c in confirmed if c.get("id")}
        by_anchor = {c["anchor"]: c for c in confirmed if c.get("anchor")}
        for o in ov:
            c = by_id.get(o["id"]) or by_anchor.get(o["anchor"])
            if c:
                o["id"], o["type"], o["title"] = c["id"], c.get("type") or o["type"], c.get("title")
    markup, regions = regions_of(html, screens, ov)
    out, index = [], {}

    def add(row):
        cid = row["id"]
        if cid in index:
            e = index[cid]
            e["uses"] += 1
            if row["screen"] not in e["screens"]:
                e["screens"].append(row["screen"])
            if not e.get("title") and row.get("title"):
                e["title"] = row["title"]
            return
        row["uses"], row["screens"] = 1, [row["screen"]]
        index[cid] = row
        out.append(row)

    for o in ov:
        span = next(((s, e) for s, e, lab in regions if lab == o["id"]), None)
        inner = markup[span[0]:span[1]] if span else ""
        # 제목: 블록 안의 첫 제목 태그(h1~h4) — 바인딩이면 그 표현식({{ sheetTitle }}) 그대로, 없으면 id
        heads = [htmlmod.unescape(re.sub(r"\s+", " ", _TAG.sub("", h))).strip() for h in re.findall(r"<h[1-4]\b[^>]*>(.*?)</h[1-4]>", inner, re.S)]
        head = next((h for h in heads if h), "")
        # 열고 닫는 핸들러 — 본문에서 anchor 키에 넣는 리터럴로 판정 (true → open · false → close · !s.x → toggle 도 open)
        opens, closes = [], []
        for name, body in bodies:
            for val in re.findall(r"\b%s:\s*(true|false|!)" % re.escape(o["anchor"] or "\0"), body):
                (closes if val == "false" else opens).append(name)
        outer = [r for r in regions if r[2] != o["id"]]
        add({"id": o["id"], "type": o["type"], "title": o.get("title") or head or o["id"],
             "screen": screen_at(outer, span[0]) if span else "shared", "anchor": o["anchor"], "style": o["style"],
             "open": sorted(set(opens)), "close": sorted(set(closes))})

    stack = []
    for m in _TAG.finditer(markup):
        closing, tag, attrs, selfclose = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        if closing:
            for i in range(len(stack) - 1, -1, -1):
                if stack[i][0] == tag:
                    del stack[i:]
                    break
            continue
        if tag in ("svg", "path", "circle", "rect", "line", "polyline", "polygon", "g", "use"):
            continue
        void = bool(selfclose) or tag in _VOID
        if not void:
            stack.append((tag, attrs if tag == "sc-for" else ""))
        ons = [(a or b, h) for a, b, h in _ON_ATTR.findall(attrs)]
        is_input = tag in ("input", "textarea", "select")
        if not ons and not is_input:
            continue
        scr = screen_at(regions, m.start())
        click = next((h for ev, h in ons if ev.lower() == "click"), None)
        inp = next((h for ev, h in ons if ev.lower() in ("input", "change")), None)
        press = sorted({ev for ev, _ in ons if ev.lower().startswith(("mouse", "touch", "pointer"))})
        label = "" if void else _label_of(markup[m.end():_inner_end(markup, tag, m.end())])
        loop = None
        for t, a in reversed(stack):
            fa = _FOR_ATTR.search(a) if t == "sc-for" else None
            if fa:
                loop = (fa.group(1), fa.group(2))
                break
        handler = inp or click or (next((h for _, h in ons), None))
        if not handler:
            continue
        tail = handler.split(".")[-1]
        in_loop = "." in handler and loop is not None and handler.split(".")[0] == loop[1]
        row = {"screen": scr, "handler": handler}
        if is_input:
            row["type"] = "slider" if (_attr(attrs, "type") or "").lower() == "range" else "input"
            row["id"] = slug(f"{loop[0]} {tail}") if in_loop else tail
            row["title"] = _attr(attrs, "placeholder") or (loop[0] if in_loop else tail)
            for k in ("placeholder", "maxlength", "min", "max"):
                if _attr(attrs, k) is not None:
                    row[k] = _attr(attrs, k)
            bind = _attr(attrs, "value") or ""
            if bind.startswith("{{"):
                row["bind"] = bind.strip("{} ")
        elif in_loop:
            row["type"] = "toggle" if tail.startswith("toggle") else "item"
            row["id"] = slug(f"{loop[0]} {tail}")
            row["title"] = label or loop[0]
            row["list"] = loop[0]
        elif click and "." not in click:
            sets = beh["handlers"].get(click, {}).get("sets", [])
            if re.match(r"set(Sub)?Tab", click) or click in beh["tab_transitions"]:
                row["type"] = "tab"
                t = beh["tab_transitions"].get(click) or {}
                row["target"] = (_screen_for(screens, t["tab"]) or t["tab"]) if "tab" in t else (t or None)
            elif click.startswith("toggle") and not any(_OPEN_KEY.search(k) for k in sets):
                row["type"] = "toggle"
            else:
                row["type"] = "button"
            row["id"], row["title"] = click, label or click
            if sets:
                row["sets"] = sets
            if not label and "<svg" in markup[m.end():m.end() + 600]:
                row["icon"] = True
        elif press and not click:
            row["type"] = "gesture"
            row["id"], row["title"], row["events"] = tail, tail, press
        else:
            continue
        add(row)
    return out


def navigation(html, screens, comps):
    """{entry, tabs{handler: screen}, transitions[{via, to, action}]} — 핸들러가 state 리터럴로 일으키는 전이.
    action: go(화면으로) · leave(화면에서) · open/close/toggle(오버레이)."""
    script = _script_of(html)
    state = entities(html).get("_state") or {}
    beh = behavior(html)

    def screen_for(key):
        return _screen_for(screens, key)

    def is_key(s):
        a = s.get("anchor") or s["id"]
        return a if a.startswith("is") else "is" + a[:1].upper() + a[1:]

    entry = next((s["id"] for s in screens if state.get(is_key(s)) is True), None) or (screens[0]["id"] if screens else None)
    by_anchor = {c["anchor"]: c["id"] for c in comps if c.get("anchor")}
    tabs = {h: (screen_for(t["tab"]) or t["tab"]) for h, t in beh["tab_transitions"].items() if "tab" in t}
    trans = []
    for name, body in _handler_bodies(script):
        for key, val in _LIT_SET.findall(body):
            if key == "tab" and val.startswith("'"):
                trans.append({"via": name, "to": screen_for(val.strip("'")) or val.strip("'"), "action": "go"})
            elif key in by_anchor:
                trans.append({"via": name, "to": by_anchor[key],
                              "action": "open" if val == "true" else "close" if val == "false" else "toggle"})
            elif key.startswith("is") and screen_for(key) and val in ("true", "false"):
                trans.append({"via": name, "to": screen_for(key), "action": "go" if val == "true" else "leave"})
    trans += [{"via": h, "to": to, "action": "go"} for h, to in tabs.items()]
    seen, out = set(), []
    for t in trans:
        k = (t["via"], t["to"], t["action"])
        if k not in seen:
            seen.add(k)
            out.append(t)
    return {"entry": entry, "tabs": tabs, "transitions": out}


# ─────────────────────────── intent · rules ───────────────────────────

def intent(chat_text):
    """대화 기록(JSON, 잘렸을 수 있다)에서 사용자 턴만 시간순으로. 파서를 쓰지 않는다."""
    raw = htmlmod.unescape(chat_text)
    turns = []
    pairs = [(c, r) for c, r in re.findall(r'"content":"((?:[^"\\]|\\.)*)"[^{}]*?"role":"(user|assistant)"', raw)]
    pairs += [(c, r) for r, c in re.findall(r'"role":"(user|assistant)"[^{}]*?"content":"((?:[^"\\]|\\.)*)"', raw)]
    for content, role in pairs:
        if role != "user" or content.startswith("Continuing from"):
            continue
        t = content.encode("utf-8", "ignore").decode("unicode_escape", "ignore").encode("latin-1", "ignore").decode("utf-8", "ignore") \
            if "\\u" in content else content
        t = t.replace("\\n", " ").replace('\\"', '"')
        t = re.sub(r"\s+", " ", t).strip()
        if t and (not turns or turns[-1] != t):
            turns.append(t)
    return turns


def rules(adherence):
    """_adherence.oxlintrc.json → {no_hex, no_px, fonts[]}"""
    out = {"no_hex": False, "no_px": False, "fonts": []}
    if not isinstance(adherence, dict):
        return out
    text = json.dumps(adherence)
    out["no_hex"] = "#[0-9a-fA-F]" in text
    out["no_px"] = "px" in text and "Raw px" in text
    x = adherence.get("x-omelette") or {}
    out["fonts"] = [str(f) for f in (x.get("fontFamilies") or [])]
    return out


# ─────────────────────────── layout — 화면별 무손실 레이아웃 트리 ───────────────────────────
# 에이전트가 HTML 을 다시 읽어 화면을 "재구성"하지 않도록, 화면 구역의 마크업을 노드 트리로 그대로 옮긴다.
# 스타일 속성은 하나도 버리지 않는다(무손실). 토큰(var(--x) · 값이 맞는 hex · 종류가 맞는 px)은 상수 이름으로,
# 문구는 Strings 상수로, svg 는 Icons 이름으로 바꾼다. 실험(2026-09-04): 트리를 받은 빌더가 분기·바인딩을
# 덜 빠뜨리고 토큰 4할을 덜 썼다. 빠뜨린 속성은 곧 충실도 손실이므로 KEEP 목록을 두지 않는다.

_CSS_VAR = re.compile(r"var\(--([a-z0-9-]+)\)", re.I)
_PX_TOKEN = re.compile(r"^-?(\d+(?:\.\d+)?)px$")
_HEX_TOKEN = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")
_BIND_EXPR = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_SPACE_PROPS = ("gap", "row-gap", "column-gap", "padding", "margin", "inset", "top", "left", "right", "bottom")
_RADIUS_PROPS = ("border-radius", "border-top-left-radius", "border-top-right-radius", "border-bottom-left-radius", "border-bottom-right-radius")


def dim_kind(key):
    """치수 토큰 키 → 문맥 종류. radius.* → radius, space.*/spacing.*/gap.* → space, 그 외 None."""
    k = str(key).lower()
    if "radius" in k or k.startswith("corner"):
        return "radius"
    if k.startswith(("space", "spacing", "gap", "inset")):
        return "space"
    return None


def _prop_kind(prop):
    p = prop.lower()
    if p in _RADIUS_PROPS:
        return "radius"
    if p in _SPACE_PROPS or p.startswith(("padding-", "margin-")):
        return "space"
    return None


class _TokenMap:
    def __init__(self, tokens):
        from . import gen                                   # gen 은 derive 를 import 한다 — 지연 import
        self.consts = gen.token_consts(tokens)
        self.by_var = {k.replace(".", "-"): k for k in tokens}
        self.dims = {}
        self.colors = {}
        for k, v in tokens.items():
            if v.get("kind") == "dimension":
                try:
                    self.dims.setdefault(float(v["value"]), k)
                except (TypeError, ValueError):
                    pass
            elif v.get("kind") == "color" and isinstance(v.get("value"), str) and v["value"].startswith("#"):
                self.colors.setdefault(v["value"].lower()[:7], k)

    def value(self, prop, val):
        """CSS 값 문자열 → 토큰 상수가 있는 조각만 바꾼 문자열. 바인딩({{ }})은 손대지 않는다."""
        if "{{" in val:
            return val.strip()
        out = _CSS_VAR.sub(lambda m: self.consts.get(self.by_var.get(m.group(1).lower(), ""), m.group(0)), val.strip())
        kind = _prop_kind(prop)
        parts = []
        for tok in out.split():
            m = _PX_TOKEN.match(tok)
            if m and kind and not tok.startswith("-"):
                key = self.dims.get(float(m.group(1)))
                if key and dim_kind(key) == kind:
                    parts.append(self.consts[key])
                    continue
            if _HEX_TOKEN.match(tok) and tok.lower()[:7] in self.colors:
                parts.append(self.consts[self.colors[tok.lower()[:7]]])
                continue
            parts.append(tok)
        return " ".join(parts)


class _Node:
    def __init__(self, tag, attrs):
        self.tag, self.attrs, self.children, self.text = tag, dict(attrs), [], []


class _TreeParser(htmlparser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        n = _Node(tag, attrs)
        self.stack[-1].children.append(n)
        if tag not in _VOID:
            self.stack.append(n)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Node(tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if data.strip():
            self.stack[-1].text.append(re.sub(r"\s+", " ", data).strip())


def _screen_slice(markup, regions, sc):
    """화면의 마크업 구역. 상태 분기 구역 → 그 anchor 의 sc-if 블록 → 파일 전체."""
    best = None
    for start, end, label in regions:
        if label == sc["id"] and (best is None or end - start > best[1] - best[0]):
            best = (start, end)
    if best:
        return markup[best[0]:best[1]]
    anchor = sc.get("anchor")
    if anchor:
        m = re.search(r'<sc-if\s+value="\{\{\s*' + re.escape(anchor) + r'\s*\}\}"', markup)
        if m:
            depth = 0
            for t in re.finditer(r"<sc-if\b|</sc-if>", markup[m.start():]):
                depth += 1 if t.group(0).startswith("<sc-if") else -1
                if depth == 0:
                    return markup[m.start():m.start() + t.end()]
        m = re.search(r'<[a-z][^>]*\bid="' + re.escape(anchor.lstrip("#")) + r'"', markup)
        if m:
            return markup[m.start():]
    body = re.search(r"<body\b[^>]*>(.*)</body>", markup, re.S)
    return body.group(1) if body else markup


def _svg_hash(svg):
    paths = "|".join(sorted(re.findall(r'\bd="([^"]+)"', svg) + re.findall(r"<(?:circle|rect)\b[^>]*>", svg)))
    return hashlib.sha1(paths.encode()).hexdigest()[:6] if paths else None


def _node_json(n, screen, tm, str_index, icon_by_hash, raw_markup):
    from . import gen
    out = {}
    tag = n.tag
    if tag == "sc-if":
        out["kind"] = "if"
        out["when"] = (n.attrs.get("value") or "").strip("{} ")
        if "hint-placeholder-val" in n.attrs:
            out["default"] = (n.attrs.get("hint-placeholder-val") or "").strip("{} ")
    elif tag == "sc-for":
        out["kind"] = "list"
        out["items"] = (n.attrs.get("list") or "").strip("{} ")
        out["as"] = n.attrs.get("as")
        if "hint-placeholder-count" in n.attrs:
            out["placeholder_count"] = n.attrs.get("hint-placeholder-count")
    elif tag == "svg":
        out["kind"] = "icon"
        h = _svg_hash(raw_markup) if raw_markup else None
        name = icon_by_hash.get(h)
        out["icon"] = f"Icons.{gen.ident(name)}" if name else "raw-svg"
        for k in ("width", "height", "viewbox"):
            if k in n.attrs:
                out[k] = n.attrs[k]
        n = _Node(tag, {k: v for k, v in n.attrs.items() if k == "style"})   # 자식 path 는 아이콘 에셋에 있다 — 트리에 안 싣는다
    elif tag == "input":
        out["kind"] = "input"
        for k in ("type", "min", "max", "step", "value", "placeholder"):
            if k in n.attrs:
                out[k] = n.attrs[k]
    elif tag == "img":
        out["kind"] = "image"
        out["src"] = n.attrs.get("src")
        if "alt" in n.attrs:
            out["alt"] = n.attrs["alt"]
    style = {}
    for part in (n.attrs.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            k = k.strip().lower()
            if k:
                style[k] = tm.value(k, v)
    if "kind" not in out:
        disp, fd = style.get("display"), style.get("flex-direction")
        if disp in ("flex", "inline-flex"):
            out["kind"] = "column" if fd and fd.startswith("column") else "row"
        elif disp == "grid":
            out["kind"] = "grid"
        elif n.text and not n.children:
            out["kind"] = "text"
        elif not n.children:
            out["kind"] = "box"
        else:
            out["kind"] = "group"
    if tag not in ("sc-if", "sc-for", "div", "span"):
        out["tag"] = tag
    for k, v in n.attrs.items():
        if k in ("style", "value", "list", "as", "hint-placeholder-val", "hint-placeholder-count", "hint-size"):
            continue
        m = re.match(r"sc-camel-on-([a-z-]+)$", k)
        if m:
            out["on_" + m.group(1).replace("-", "_")] = (v or "").strip("{} ")
        elif k.startswith("on") and v and "{{" in v:
            out["on_" + k[2:].lower()] = v.strip("{} ")
        elif k in ("class", "id", "role", "href", "aria-label", "title") or k.startswith(("data-", "hint-", "component-", "from")):
            out[k] = v if v is not None else True
    text = " ".join(n.text)
    if text:
        names = []

        def _ph(b):
            nm = re.split(r"[^\w]", b.group(1).strip())[-1] or "value"
            nm = nm[:1].lower() + nm[1:]
            names.append(nm)
            return "{" + nm + "}"
        fmt = _BIND_EXPR.sub(_ph, text)
        lit = re.sub(r"\{\w+\}", "", fmt).strip()
        key = (screen, fmt if names else text)
        const = str_index.get(key) or str_index.get(("*", fmt if names else text))
        if const:
            out["text"] = const
            if names:
                out["params"] = names
        elif names and not lit:
            out["bind"] = names if len(names) > 1 else names[0]
        else:
            out["text"] = text
            out["raw_text"] = True                           # Strings 에 없는 문구 — 파생이 못 잡은 것. 사람 확인 대상
    if style:
        out["style"] = style
    kids = [_node_json(c, screen, tm, str_index, icon_by_hash, c._raw if hasattr(c, "_raw") else None) for c in n.children]
    kids = [k for k in kids if k]
    if kids:
        out["children"] = kids
    return out


def layout(html, screens, comps, tokens, string_rows, icon_rows):
    """{screen_id: 트리} — 화면 구역의 마크업을 무손실로. 스타일 속성은 전부 싣고, 토큰·문구·아이콘만 상수 이름으로 바꾼다."""
    from . import gen
    markup, regions = regions_of(html, list(screens), comps)
    tm = _TokenMap(tokens or {})
    str_index = {}
    for r in string_rows:
        grp, _, leaf = r["key"].partition(".")
        const = f"Strings.{gen.ident(grp, upper=True)}.{gen.ident(leaf.replace('.', ' '))}"
        str_index.setdefault((r["screen"], r["text"]), const)
        str_index.setdefault(("*", r["text"]), const)
    icon_by_hash = {i["hash"]: i["name"] for i in icon_rows if i.get("hash")}
    out = {}
    for sc in screens:
        piece = _screen_slice(markup, regions, sc)
        # svg 원문을 노드에 붙여 해시로 아이콘을 찾는다
        svgs = [m.group(0) for m in _SVG.finditer(piece)]
        p = _TreeParser()
        p.feed(piece)
        it = iter(svgs)

        def _attach(node):
            if node.tag == "svg":
                node._raw = next(it, None)
            for c in node.children:
                _attach(c)
        for c in p.root.children:
            _attach(c)
        roots = [_node_json(c, sc["id"], tm, str_index, icon_by_hash, None) for c in p.root.children]
        roots = [r for r in roots if r]
        out[sc["id"]] = roots[0] if len(roots) == 1 else {"kind": "group", "children": roots}
    return out


# ─────────────────────────── 조립 ───────────────────────────

def _count_nodes(t):
    return 1 + sum(_count_nodes(c) for c in t.get("children") or [])


def _find(d, name):
    for base, dirs, names in os.walk(d):
        dirs[:] = [x for x in dirs if x != DERIVED_DIR]
        if name in names:
            return os.path.join(base, name)
    return None


def write_all(root, manifest):
    """design/derived/* 를 (다시) 쓴다. 반환: 요약 dict. 실패한 갈래는 비워 두되 요약에 적는다."""
    d = util.design_dir(root)
    out_dir = os.path.join(d, DERIVED_DIR)
    os.makedirs(out_dir, exist_ok=True)
    summary = {"strings": 0, "icons": 0, "entities": [], "handlers": 0, "components": {}, "intent_turns": 0, "rules": {}}
    screens = manifest.get("screens") or []
    by_file = {}
    for sc in screens:
        if sc.get("file"):
            by_file.setdefault(sc["file"], []).append(sc)
    ents, beh, strs, ics, comps = {}, {"handlers": {}, "tab_transitions": {}, "timers_ms": []}, [], [], []
    nav = {"entry": None, "tabs": {}, "transitions": []}
    confirmed = manifest.get("components") if manifest.get("components_confirmed") else None
    seen_text, seen_icon, seen_comp = set(), {}, {}
    htmls, comps_by_file = {}, {}
    for f, scs in by_file.items():
        html = util.read_text(os.path.join(d, f))
        if not html:
            continue
        htmls[f] = html
        e = entities(html)
        ents.update({k: v for k, v in e.items() if k not in ents})
        b = behavior(html)
        beh["handlers"].update(b["handlers"])
        beh["tab_transitions"].update(b["tab_transitions"])
        beh["timers_ms"] = sorted(set(beh["timers_ms"]) | set(b["timers_ms"]))
        cs = components(html, scs, [c for c in (confirmed or []) if not c.get("file") or c["file"] == f])
        comps_by_file[f] = cs
        # 파일 하나 = 화면 하나(상태 분기 없음)면 그 파일의 문구 전부가 그 화면 것이다
        single = scs[0]["id"] if len(scs) == 1 and not scs[0].get("anchor") else None
        for r in strings(html, scs, cs):
            if r["text"] in seen_text:
                continue
            seen_text.add(r["text"])
            if single and r["screen"] == "shared":
                r["screen"] = single
                r["key"] = f"{single}.{r['key'].split('.', 1)[1]}"
            strs.append(r)
        for ic in icons(html, scs, cs):
            if ic["hash"] in seen_icon:
                seen_icon[ic["hash"]]["uses"] += ic["uses"]
                continue
            if single:
                ic["screens"] = [single if x == "shared" else x for x in ic["screens"]]
            seen_icon[ic["hash"]] = ic
            ics.append(ic)
        for c in cs:
            if single:
                c["screen"] = single if c["screen"] == "shared" else c["screen"]
                c["screens"] = [single if x == "shared" else x for x in c["screens"]]
            c["file"] = f
            if c["id"] in seen_comp:
                seen_comp[c["id"]] += 1
                c["id"] = f"{c['id']}{seen_comp[c['id']]}"
            else:
                seen_comp[c["id"]] = 1
            comps.append(c)
        n = navigation(html, scs, cs)
        nav["entry"] = nav["entry"] or n["entry"]
        nav["tabs"].update(n["tabs"])
        nav["transitions"] += n["transitions"]
    used = {}
    for r in strs:
        k = r["key"]
        if k in used:
            used[k] += 1
            r["key"] = f"{k}{used[k]}"
        else:
            used[k] = 1
    used = {}
    for ic in ics:
        n = ic["name"]
        if n in used:
            used[n] += 1
            ic["name"] = f"{n}{used[n]}"
        else:
            used[n] = 1
    ics.sort(key=lambda i: i["name"])
    util.write_json(os.path.join(out_dir, "entities.json"), ents)
    summary["entities"] = [k for k in ents if not k.startswith("_")]
    util.write_json(os.path.join(out_dir, "behavior.json"), beh)
    summary["handlers"] = len(beh["handlers"])
    util.write_json(os.path.join(out_dir, "strings.json"), {"strings": strs})
    summary["strings"] = len(strs)
    ic_dir = os.path.join(out_dir, "icons")
    os.makedirs(ic_dir, exist_ok=True)
    for n in os.listdir(ic_dir):
        os.remove(os.path.join(ic_dir, n))
    for ic in ics:
        util.write_text(os.path.join(ic_dir, f"{ic['name']}.svg"), ic["svg"] + "\n")
    util.write_json(os.path.join(out_dir, "icons.json"),
                    {"icons": [{"name": i["name"], "file": f"icons/{i['name']}.svg", "uses": i["uses"], "screens": i["screens"]} for i in ics]})
    summary["icons"] = len(ics)
    util.write_json(os.path.join(out_dir, "components.json"), {"components": comps})
    for c in comps:
        summary["components"][c["type"]] = summary["components"].get(c["type"], 0) + 1
    util.write_json(os.path.join(out_dir, "navigation.json"), nav)
    summary["navigation"] = {"entry": nav["entry"], "tabs": len(nav["tabs"]), "transitions": len(nav["transitions"])}
    # 매니페스트(handoff.manifest.json)가 상세를 담을 때 쓰는 재료 — 응답에는 싣지 않는다 (tools 가 pop)
    summary["_detail"] = {
        "components": comps, "navigation": nav, "state": ents.get("_state") or {},
        "entities": {k: {"count": len(v), "fields": sorted({f for x in v if isinstance(x, dict) for f in x})}
                     if isinstance(v, list) else {"keys": sorted(v)} if isinstance(v, dict) else {"value": v}
                     for k, v in ents.items() if not k.startswith("_")},
        "strings": [{"key": r["key"], "screen": r["screen"]} for r in strs],
        "icons": [{"name": i["name"], "screens": i["screens"]} for i in ics]}
    # 레이아웃 트리 — 문구·아이콘이 다 모인 뒤에
    lay_dir = os.path.join(out_dir, "layout")
    shutil.rmtree(lay_dir, ignore_errors=True)
    os.makedirs(lay_dir, exist_ok=True)
    summary["layout"] = {}
    for f, scs in by_file.items():
        if f not in htmls:
            continue
        try:
            trees = layout(htmls[f], scs, comps_by_file.get(f) or [], manifest.get("tokens") or {}, strs, ics)
        except Exception as e:                              # 트리는 보조물 — 실패해도 파생 전체를 막지 않는다
            summary["layout"]["_error"] = f"{type(e).__name__}: {e}"
            continue
        for sid, tree in trees.items():
            util.write_json(os.path.join(lay_dir, f"{sid}.json"), tree)
            summary["layout"][sid] = _count_nodes(tree)
    turns = []
    for c in manifest.get("chats") or []:
        turns += intent(util.read_text(os.path.join(d, c)))
    util.write_text(os.path.join(out_dir, "intent.md"),
                    "# Designer's intent — user turns from the Claude Design conversation (oldest first)\n\n"
                    + ("".join(f"- {t}\n" for t in turns) if turns else "(no conversation found)\n"))
    summary["intent_turns"] = len(turns)
    adh = _find(d, "_adherence.oxlintrc.json")
    r = rules(util.read_json(adh) if adh else None)
    util.write_json(os.path.join(out_dir, "rules.json"), r)
    summary["rules"] = r
    return summary


def read(root, name, default=None):
    return util.read_json(os.path.join(util.design_dir(root), DERIVED_DIR, name), default)
