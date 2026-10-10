"""공용 — 경로 · 설정 · 상태 · 해시.

레포 안의 자리:
  design/             Claude Design 핸드오프 패키지 원본 (읽기 전용 — 프론트의 정본)
  api/openapi.yaml    백엔드 계약 (읽기 전용 — 잠금 뒤 바꾸면 재승인)
  shared/generated/   서버가 만든 상수 (워크트리마다 놓는다 — 손대면 검사가 잡는다)
  .handoff/           도구 상태 · 리포트 · 워크트리 (커밋하지 않는다)
  docs/               사람용 문서 (md · 화면 대조)
"""
import datetime
import hashlib
import json
import os

HO_DIR = ".handoff"
CONFIG = "config.json"           # 이 파일이 있으면 "배선된 레포"
STATE = "state.json"             # 서버만 쓴다
SPEC = "spec.json"
HANDOFF = "handoff.json"         # 루프 인계
REPORTS = "reports"              # .handoff/reports/<role>.json
WORKTREES = "worktrees"          # .handoff/worktrees/<role>
SHOTS = "shots"                  # <worktree>/.handoff/shots/<screen>.png

DESIGN_DIR = "design"
API_DIR = "api"
SPEC_SEED = "handoff.spec.json"  # 레포 루트 — 커밋된 스펙 기본값. 그래프가 인터뷰 대신 먼저 넣고, 계약 확정 커밋에 함께 들어간다
API_FILE = "openapi.yaml"
GEN_DIR = os.path.join("shared", "generated")
DOCS_DIR = "docs"

ROLES = ("backend", "ios", "android", "web")
APPS = ("ios", "android", "web")
MOBILE = ("ios", "android")          # 파리티 짝 — web 은 모바일 합집합과 따로 대조한다 (score.evaluate)
TARGETS = ("mobile", "web", "mixed")  # 디자인 대상 — design.scan 이 패키지에서 읽고, 사람이 import_design(target=) 로 덮는다

DEFAULTS = {
    "roles": {"backend": "backend", "ios": "apps/ios", "android": "apps/android", "web": "apps/web"},
    "kotlin_package": "shared.generated",
    "score": {
        "threshold": 85,
        "weights": {"consumption": 0.4, "tests": 0.3, "parity": 0.3},
        "hardcode_penalty": 2,
        "divergence_penalty": 10,
    },
    # require_runtime: true 면 런타임 증거(서버가 돌린 테스트 또는 빌드 성공 리포트)가 없는 역할이 있을 때 ship 을 거부한다.
    # 기본 false — 툴체인 없는 기기에서는 정적 검사만으로 pass_static 을 내고, 런타임 검증은 상태(runtime_pending)에 남긴다.
    # require_complete: true(기본) 면 계약 항목(라우트·화면·아이콘·문구·핸들러)을 하나라도 안 소비한 리포트를 접수하지 않는다 —
    # spec.divergences 로 승인된 항목과 status=blocked 만 예외. 파리티 갭은 접수 단계에서 구조적으로 막힌다 (끝나고 loop 가 아니라).
    "verify": {"commands": {"backend": [], "ios": [], "android": [], "web": []}, "timeout_sec": 600, "require_runtime": False,
               "require_complete": True},
    # builder.command: 그래프(graph.advance)가 역할을 착수할 때 서버가 직접 띄우는 셸 명령. 착수 프롬프트를 stdin 으로 받고
    # cwd 는 워크트리, 환경 변수 HANDOFF_ROOT · HANDOFF_ROLE · HANDOFF_WORKTREE. 비어 있으면 클라이언트(스킬)에 과제로 넘긴다.
    "builder": {"command": "", "timeout_sec": 7200},
    # 착수 방식. parallel 이 기본이다 — 빌더 한 턴의 대부분은 LLM 시간이고 컴파일은 끝에 잠깐이라, 역할을 동시에 띄우는 쪽이
    # 벽시계 시간을 줄인다. 한 기기에서 iOS·Android 빌드가 코어를 심하게 나눠 쓰면 "serial" 로 바꾼다 (order 순서로 하나씩).
    "dispatch": {"mode": "parallel", "order": ["backend", "ios", "android", "web"]},
    "test_globs": [
        "**/test_*.py", "**/*_test.py", "**/tests/**/*.py",
        "**/*.test.ts", "**/*.test.tsx", "**/*.test.js", "**/*.spec.ts", "**/*.spec.js",
        "**/*Tests.swift", "**/*Test.swift",
        "**/*Test.kt", "**/*Tests.kt", "**/src/test/**", "**/src/androidTest/**",
        "**/*_test.go",
    ],
}


# ─────────────────────────── 경로 ───────────────────────────

def ho(root, *parts):
    return os.path.join(root, HO_DIR, *parts)


def design_dir(root):
    return os.path.join(root, DESIGN_DIR)


def api_path(root):
    return os.path.join(root, API_DIR, API_FILE)


def worktree(root, role):
    return ho(root, WORKTREES, role)


def is_wired(root):
    return os.path.isfile(ho(root, CONFIG))


# ─────────────────────────── 설정 ───────────────────────────

def _merge(base, user):
    out = dict(base)
    for k, v in (user or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        elif k in out and isinstance(v, type(out[k])):
            out[k] = v
    return out


def load_config(root):
    return _merge(DEFAULTS, read_json(ho(root, CONFIG), {}) or {})


def write_config(root, cfg=None):
    write_json(ho(root, CONFIG), cfg or DEFAULTS)


# ─────────────────────────── 상태 ───────────────────────────

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


EMPTY_STATE = {
    "phase": "import", "version": 0, "cycle": 1,
    "locked": None,          # {"version": n, "hash": 지문} — 승인 시점의 design/+api/ 지문
    "roles": [],             # 이번 루프에 착수한 역할
    # "reports" 는 저장하지 않는다 — .handoff/reports/<role>.json 의 존재(+ _version 일치)에서 flow.current() 가 매번 derive 한다.
    # 병렬 빌더가 report 를 동시에 내도 각자 자기 파일만 쓰므로 유실이 없고, 결산(verify)은 주행자 하나가 한다.
    "verdict": None,         # 마지막 verify 결과 (loop | pass_static | pass)
    "runtime_pending": [],   # 런타임 증거 없이 머지된 역할 — 툴체인 있는 기기에서 나중에 검증한다
    "design_source": None,   # {"path", "url", "project_id"} — 패키지 출처 (요약 표에 보인다)
    "history": [],           # 서버가 적는 사건 기록 (감사용)
}


def read_state(root):
    st = read_json(ho(root, STATE), None)
    if not isinstance(st, dict):
        return dict(EMPTY_STATE, history=[])
    return {**EMPTY_STATE, **st}


def write_state(root, st):
    write_json(ho(root, STATE), st)


def reported_roles(root, version):
    """이 계약 버전의 리포트 파일이 있는 역할 — 접수 목록의 유일한 출처. 다른 버전의 파일은 없는 것으로 친다."""
    d = ho(root, REPORTS)
    out = []
    if os.path.isdir(d):
        for n in sorted(os.listdir(d)):
            if n.endswith(".json"):
                rec = read_json(os.path.join(d, n))
                if isinstance(rec, dict) and rec.get("_version") == version:
                    out.append(n[:-5])
    return out


def clear_reports(root, roles=None):
    """리포트 파일 삭제 — roles 가 없으면 전부. loop 재착수(그 역할만) · back(전부) 이 쓴다."""
    d = ho(root, REPORTS)
    if not os.path.isdir(d):
        return
    for n in os.listdir(d):
        if n.endswith(".json") and (roles is None or n[:-5] in roles):
            os.remove(os.path.join(d, n))


def record(root, st, ev, **fields):
    """상태에 사건 한 줄을 남기고 저장한다. 자유 텍스트는 마스킹한다."""
    from . import leaks
    st.setdefault("history", []).append({"t": now(), "ev": ev, **leaks.mask_all_deep(fields)})
    st["history"] = st["history"][-500:]
    write_state(root, st)
    return st


# ─────────────────────────── 파일·해시 ───────────────────────────

def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def read_text(path, default=""):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return default


def write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hash(directory):
    """디렉토리 전체의 결정적 해시 (상대경로 + 내용). 없으면 빈 문자열."""
    if not os.path.isdir(directory):
        return ""
    h = hashlib.sha256()
    for base, dirs, names in os.walk(directory):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for n in sorted(names):
            if n.startswith("."):
                continue
            p = os.path.join(base, n)
            rel = os.path.relpath(p, directory).replace(os.sep, "/")
            h.update(rel.encode())
            h.update(b"\0")
            h.update(sha_file(p).encode())
            h.update(b"\n")
    return h.hexdigest()


def fingerprint(root):
    """design/ + api/ 의 결합 지문 12자 — 승인 프롬프트와 요약 표에 같은 값이 찍힌다."""
    d = tree_hash(design_dir(root))
    a = sha_file(api_path(root)) if os.path.isfile(api_path(root)) else ""
    if not d and not a:
        return "(없음)"
    return hashlib.sha256(f"{d}\n{a}".encode()).hexdigest()[:12]


def read_spec(root):
    return read_json(ho(root, SPEC), None)


def platforms(root):
    spec = read_spec(root) or {}
    plats = {str(p).lower() for p in (spec.get("platforms") or [])}
    return [p for p in APPS if p in plats]


def active_roles(root):
    return ["backend"] + platforms(root)
