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
import re

from . import util

DERIVED_DIR = "derived"
_STATE_OPEN = re.compile(r'<sc-if\s+value="\{\{\s*(\w+)\s*\}\}"')
_TEXT_NODE = re.compile(r">([^<>{}]*[^\s<>{}][^<>{}]*)<")
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


# ─────────────────────────── 조립 ───────────────────────────

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
    for f, scs in by_file.items():
        html = util.read_text(os.path.join(d, f))
        if not html:
            continue
        e = entities(html)
        ents.update({k: v for k, v in e.items() if k not in ents})
        b = behavior(html)
        beh["handlers"].update(b["handlers"])
        beh["tab_transitions"].update(b["tab_transitions"])
        beh["timers_ms"] = sorted(set(beh["timers_ms"]) | set(b["timers_ms"]))
        cs = components(html, scs, [c for c in (confirmed or []) if not c.get("file") or c["file"] == f])
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
