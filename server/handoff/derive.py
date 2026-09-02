"""파생 — 디자인 자료에서 네이티브 구현이 쓸 데이터를 결정적으로 뽑아 design/derived/ 에 쓴다.

  intent.md        대화 기록의 사용자 턴 (의도·결정 로그) — 화면 HTML 만으론 안 보이는 동작 규칙
  entities.json    스크립트의 데이터 배열 (도메인 모델 + 시드) · 초기 state
  strings.json     마크업 문구 + 데이터 문구 → Strings.* 로 생성된다
  icons.json + icons/*.svg   인라인 SVG 를 path 로 중복 제거 → Icons.* 로 생성된다
  behavior.json    핸들러 → 바꾸는 state 키 · 탭 전이 (두 플랫폼이 같은 전이표를 구현하는지가 파리티 기준)
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


def behavior(html):
    script = _script_of(html)
    handlers = {}
    for m in re.finditer(r"^\s{2}(\w+)\s*=\s*\([^)]*\)\s*=>\s*(.*?)(?=^\s{2}\w+\s*=\s*\(|^\s{2}renderVals\(|\Z)",
                         script, re.S | re.M):
        name, body = m.group(1), m.group(2)
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


def _overlay_ids(screens):
    """style="{{ nowPlayingStyle }}" 같은 블록 → 화면 id. anchor 'nowPlayingOpen' 의 줄기 'nowplaying' 로 맞춘다."""
    out = {}
    for s in screens:
        a = (s.get("anchor") or "")
        if a and not a.startswith("is"):
            out[re.sub(r"(open|visible|shown)$", "", a.lower())] = s["id"]
    return out


def screen_regions(markup, screens):
    """[(start, end, screen_id)] — 텍스트/아이콘의 화면 귀속에 쓴다. 안쪽 구역이 우선한다."""
    state_ids = {s.get("anchor"): s["id"] for s in screens if s.get("anchor")}
    overlays = _overlay_ids(screens)
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
        else:
            v = re.search(r'style="\{\{\s*(\w+)\s*\}\}', attrs)
            if v:
                stem = re.sub(r"(sheetstyle|backdropstyle|style)$", "", v.group(1).lower())
                label = overlays.get(stem) or overlays.get(stem + "sheet")
        stack.append((tag, m.start(), label))
    return sorted(regions)


def screen_at(regions, pos):
    best = None
    for start, end, label in regions:
        if start <= pos < end and (best is None or (end - start) < (best[1] - best[0])):
            best = (start, end, label)
    return best[2] if best else "shared"


def strings(html, screens):
    """[{key, text, screen, source}] — 마크업 텍스트 노드(화면 귀속) + 데이터 문구."""
    markup = _markup_of(html)
    regions = screen_regions(markup, screens)
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


def icons(html, screens=()):
    """[{name, svg, hash, uses, screens}] — path 집합으로 중복 제거. 이름은 근처 핸들러/문구에서, 없으면 해시."""
    markup = _markup_of(html)
    regions = screen_regions(markup, list(screens))
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
    summary = {"strings": 0, "icons": 0, "entities": [], "handlers": 0, "intent_turns": 0, "rules": {}}
    screens = manifest.get("screens") or []
    by_file = {}
    for sc in screens:
        if sc.get("file"):
            by_file.setdefault(sc["file"], []).append(sc)
    ents, beh, strs, ics = {}, {"handlers": {}, "tab_transitions": {}, "timers_ms": []}, [], []
    seen_text, seen_icon = set(), {}
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
        # 파일 하나 = 화면 하나(상태 분기 없음)면 그 파일의 문구 전부가 그 화면 것이다
        single = scs[0]["id"] if len(scs) == 1 and not scs[0].get("anchor") else None
        for r in strings(html, scs):
            if r["text"] in seen_text:
                continue
            seen_text.add(r["text"])
            if single and r["screen"] == "shared":
                r["screen"] = single
                r["key"] = f"{single}.{r['key'].split('.', 1)[1]}"
            strs.append(r)
        for ic in icons(html, scs):
            if ic["hash"] in seen_icon:
                seen_icon[ic["hash"]]["uses"] += ic["uses"]
                continue
            if single:
                ic["screens"] = [single if x == "shared" else x for x in ic["screens"]]
            seen_icon[ic["hash"]] = ic
            ics.append(ic)
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
