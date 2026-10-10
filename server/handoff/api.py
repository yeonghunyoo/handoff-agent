"""백엔드 계약 — api/openapi.yaml 을 읽어 라우트 목록을 낸다.

의존성을 두지 않으려고 YAML 부분집합 파서를 직접 든다: 매핑 · 시퀀스 · 스칼라 · 인용문자열 ·
블록 스칼라(| >) · 한 줄 flow([] {}) · 주석. 앵커·태그·다중 문서는 지원하지 않는다 (openapi 에 필요 없다).
JSON 으로 써도 된다 — 먼저 json 으로 읽어 본다.
"""
import json
import re

METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


class ApiError(ValueError):
    pass


# ─────────────────────────── YAML 부분집합 ───────────────────────────

def _strip_comment(line):
    out, q = [], None
    for i, ch in enumerate(line):
        if q:
            out.append(ch)
            if ch == q and (i == 0 or line[i - 1] != "\\"):
                q = None
        elif ch in "\"'":
            q = ch
            out.append(ch)
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _scalar(s):
    s = s.strip()
    if s == "" or s in ("~", "null", "Null", "NULL"):
        return None
    if s in ("true", "True", "TRUE"):
        return True
    if s in ("false", "False", "FALSE"):
        return False
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        body = s[1:-1]
        if s[0] == '"':
            body = body.encode().decode("unicode_escape") if "\\" in body else body
        else:
            body = body.replace("''", "'")
        return body
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [_scalar(x) for x in _split_flow(inner)] if inner else []
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        out = {}
        for part in _split_flow(inner):
            if ":" in part:
                k, v = part.split(":", 1)
                out[_scalar(k)] = _scalar(v)
        return out
    if re.fullmatch(r"[-+]?\d+", s):
        return int(s)
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?", s):
        return float(s)
    return s


def _split_flow(s):
    parts, depth, q, cur = [], 0, None, []
    for ch in s:
        if q:
            cur.append(ch)
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
            cur.append(ch)
        elif ch in "[{":
            depth += 1
            cur.append(ch)
        elif ch in "]}":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


_KEY_RE = re.compile(r"^(\"[^\"]*\"|'[^']*'|[^\s\"'#][^:]*?)\s*:(?:\s+|$)")


def _lines(text):
    out = []
    for raw in text.splitlines():
        if raw.strip() in ("---", "...") or raw.lstrip().startswith("#"):
            continue
        line = _strip_comment(raw.replace("\t", "  "))
        if line.strip():
            out.append((len(line) - len(line.lstrip(" ")), line.strip()))
    return out


def loads(text):
    text = text or ""
    try:
        return json.loads(text)
    except Exception:
        pass
    lines = _lines(text)
    if not lines:
        return None
    val, i = _parse(lines, 0, lines[0][0])
    return val


def _parse(lines, i, indent):
    ind, body = lines[i]
    if body.startswith("- ") or body == "-":
        return _parse_seq(lines, i, ind)
    if _KEY_RE.match(body):
        return _parse_map(lines, i, ind)
    return _scalar(body), i + 1


def _block_scalar(lines, i, indent, style):
    parts = []
    j = i
    while j < len(lines) and lines[j][0] > indent:
        parts.append(" " * (lines[j][0] - indent - 2) + lines[j][1] if lines[j][0] > indent + 2 else lines[j][1])
        j += 1
    text = ("\n" if style == "|" else " ").join(parts)
    return text, j


def _value_after_key(lines, i, indent, rest):
    """key: 뒤의 값. 같은 줄에 있으면 스칼라, 없으면 다음 더 들여쓴 블록."""
    if rest.strip() in ("|", ">", "|-", ">-"):
        return _block_scalar(lines, i + 1, indent, rest.strip()[0])
    if rest.strip():
        return _scalar(rest), i + 1
    if i + 1 < len(lines) and (lines[i + 1][0] > indent or
                               (lines[i + 1][0] == indent and lines[i + 1][1].startswith("- "))):
        return _parse(lines, i + 1, lines[i + 1][0])
    return None, i + 1


def _parse_map(lines, i, indent):
    out = {}
    while i < len(lines) and lines[i][0] == indent:
        body = lines[i][1]
        m = _KEY_RE.match(body)
        if not m:
            break
        key = _scalar(m.group(1))
        rest = body[m.end():]
        val, i = _value_after_key(lines, i, indent, rest)
        out[key] = val
    return out, i


def _parse_seq(lines, i, indent):
    out = []
    while i < len(lines) and lines[i][0] == indent and (lines[i][1].startswith("- ") or lines[i][1] == "-"):
        item = lines[i][1][2:].strip()
        if not item:
            val, i = _value_after_key(lines, i, indent, "")
            out.append(val)
            continue
        if _KEY_RE.match(item):
            # "- key: v" — 항목이 매핑이다. 이 줄을 indent+2 매핑의 첫 줄로 바꿔 읽는다
            sub = [(indent + 2, item)]
            j = i + 1
            while j < len(lines) and lines[j][0] > indent:
                sub.append(lines[j])
                j += 1
            val, _ = _parse_map(sub, 0, indent + 2)
            out.append(val)
            i = j
        else:
            out.append(_scalar(item))
            i += 1
    return out, i


# ─────────────────────────── 라우트 ───────────────────────────

def _camel(path):
    parts = []
    for seg in path.strip("/").split("/"):
        if not seg:
            continue
        if seg.startswith("{"):
            p = "".join(w[:1].upper() + w[1:] for w in seg.strip("{}").replace("-", "_").split("_") if w)
            parts.append("By" + p)
        else:
            parts.append(re.sub(r"[^0-9A-Za-z]+", " ", seg).title().replace(" ", ""))
    return "".join(parts) or "Root"


def routes(doc):
    """[{name, method, path, summary}] — operationId 가 있으면 그 이름, 없으면 method+Path."""
    if not isinstance(doc, dict):
        raise ApiError("openapi 문서가 매핑이 아니다")
    paths = doc.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise ApiError("paths 가 비어 있다")
    out, seen = [], set()
    for path, ops in paths.items():
        if not isinstance(path, str) or not path.startswith("/"):
            raise ApiError(f"경로는 / 로 시작한다: {path!r}")
        if not isinstance(ops, dict):
            raise ApiError(f"{path} 아래가 매핑이 아니다")
        for method, op in ops.items():
            m = str(method).lower()
            if m not in METHODS:
                continue
            op = op if isinstance(op, dict) else {}
            name = op.get("operationId") or (m + _camel(path))
            name = re.sub(r"[^0-9A-Za-z_]", "_", str(name))
            name = name[0].lower() + name[1:]
            if name in seen:
                raise ApiError(f"라우트 이름이 겹친다: {name} ({m.upper()} {path})")
            seen.add(name)
            out.append({"name": name, "method": m.upper(), "path": path, "explicit_id": bool(op.get("operationId")),
                        "summary": str(op.get("summary") or op.get("description") or "")[:120]})
    if not out:
        raise ApiError("메서드가 하나도 없다 (get/post/… 가 필요하다)")
    return out


def validate(text):
    """제출 본문을 검증하고 라우트를 돌려준다. 실패는 ApiError."""
    try:
        doc = loads(text)
    except Exception as e:
        raise ApiError(f"파싱 실패: {e}")
    return routes(doc)


def path_regex(template):
    """경로 템플릿을 코드 안 리터럴 대조용 정규식으로 — {id} 는 어떤 파라미터 표기든 받는다."""
    parts = re.split(r"\{[^}]+\}", template)
    return re.compile(r"[{:<$]?[\w.]+[}>]?".join(re.escape(p) for p in parts) + r"(?!\w)(?!/[^\"'\s)])")


# ─────────────────────────── 골격 초안 ───────────────────────────

def _words(name):
    return [w for w in name.lower().split("_") if w]


def _singular(word):
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word.endswith("ses") or word.endswith("xes"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _camel_words(words, upper_first):
    out = "".join(w[:1].upper() + w[1:] for w in words)
    return out if upper_first else out[:1].lower() + out[1:]


def _yaml_type(v):
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "string"


def _q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def skeleton(entities):
    """derived/entities.json 의 컬렉션(대문자 const 배열, 원소가 객체)마다 CRUD 라우트 다섯 개 + 스키마를 결정적으로 뽑은
    openapi 문서(문자열). LLM 이 백지에서 쓰지 않고 이것을 다듬게 하는 출발점이다 — 화면이 안 쓰는 라우트는 지우고 없는 것은 더한다.
    컬렉션이 없으면 None. 결과는 validate 를 통과한다."""
    cols = {k: v for k, v in (entities or {}).items()
            if not k.startswith("_") and isinstance(v, list) and v and all(isinstance(x, dict) for x in v)}
    if not cols:
        return None
    L = ["openapi: 3.0.0", 'info: {title: "API (draft from design/derived/entities.json — edit, then api_submit)", version: "1"}', "paths:"]
    S = ["components:", "  schemas:"]
    for name in sorted(cols):
        words = _words(name)
        sing = words[:-1] + [_singular(words[-1])]
        Sing, sings = _camel_words(sing, True), _camel_words(sing, False)
        plural = _camel_words(words, True)
        path = "/" + "-".join(words)
        pid = sings + "Id"
        L += [f"  {path}:",
              "    get:", f"      operationId: list{plural}", f"      summary: List {' '.join(words)}",
              "    post:", f"      operationId: create{Sing}", f"      summary: Create a {' '.join(sing)}",
              f"  {path}/{{{pid}}}:",
              "    get:", f"      operationId: get{Sing}", f"      summary: Get one {' '.join(sing)}",
              "    put:", f"      operationId: update{Sing}", f"      summary: Update a {' '.join(sing)}",
              "    delete:", f"      operationId: delete{Sing}", f"      summary: Delete a {' '.join(sing)}"]
        first = cols[name][0]
        S += [f"    {Sing}:", "      type: object", "      properties:"]
        for k in first:
            v = first[k]
            S.append(f"        {k}:")
            S.append(f"          type: {_yaml_type(v)}")
            if isinstance(v, list):
                S.append("          items:")
                S.append(f"            type: {_yaml_type(v[0]) if v else 'string'}")
            if isinstance(v, bool):
                S.append(f"          example: {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                S.append(f"          example: {v}")
            elif isinstance(v, str):
                S.append(f"          example: {_q(v)}")
    return "\n".join(L + S) + "\n"
