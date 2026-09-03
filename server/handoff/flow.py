"""단계 — 상태 파일 + 실측 대조.

  import → spec → api → review ✋ → build → verify → ship ✋ → done

state.json 은 서버만 쓴다. 그래도 믿지 않는다: 승인(잠금) 뒤 design/ 나 api/ 가 바뀌었으면
지문이 어긋나므로 review 로 강등한다 — 승인은 "그때 그 파일"에만 유효하다.
"""
import os

from . import design, util

PHASES = ["import", "spec", "api", "review", "build", "verify", "ship", "done"]
LABELS = {
    "import": "① 핸드오프 패키지 등록",
    "spec": "② 스펙 · 인프라 결정",
    "api": "③ 백엔드 계약 (openapi.yaml)",
    "review": "④ 계약 확정 — 사람 승인",
    "build": "⑤ 구현 (워크트리 병렬)",
    "verify": "⑥ 검사",
    "ship": "⑦ 완료 승인 — 사람 승인",
    "done": "완료",
}
HUMAN = {"review", "ship"}


def idx(p):
    return PHASES.index(p)


def next_action(st):
    p = st["phase"]
    if p == "import":
        return "import_design(path) — Claude Design 에서 내보낸 zip/폴더 경로를 넘긴다 (레포 루트에 두면 status.candidates 가 찾는다)"
    if p == "spec":
        return "spec_save(spec) — platforms · stack.backend · infra(db/auth/hosting/env) 를 사람에게 묻고 저장한다"
    if p == "api":
        return "api_submit(openapi) — design/ 의 화면·문서를 읽고 각 화면이 필요로 하는 데이터로 openapi.yaml 을 초안한다"
    if p == "review":
        return "review() — 사람이 계약(design/ + api/ + 스펙)을 승인한다 (elicitation). 대시보드를 먼저 보인다"
    if p == "build":
        if st.get("roles"):
            pending = [r for r in st["roles"] if r not in st.get("reports", [])]
            return f"구현 중 — 리포트 대기: {', '.join(pending) or '없음'} (각 에이전트가 precheck → report)"
        return "build() — 워크트리를 만들고 착수 프롬프트를 받아 에이전트를 병렬로 띄운다"
    if p == "verify":
        return "verify() — 서버가 재검사해 점수와 loop/pass 를 판정한다"
    if p == "ship":
        return "ship() — 사람이 승인하면 본선에 머지한다 (elicitation)"
    return "완료. 새 요구는 import_design 또는 spec_save 로 새 사이클을 연다"


def spec_problems(spec):
    out = []
    if not isinstance(spec, dict):
        return ["spec 이 객체가 아니다"]
    plats = [str(p).lower() for p in (spec.get("platforms") or [])]
    if not any(p in util.APPS for p in plats):
        out.append("platforms 에 ios/android 중 하나 이상이 필요하다")
    stack = spec.get("stack") if isinstance(spec.get("stack"), dict) else {}
    if not stack.get("backend"):
        out.append("stack.backend 가 비었다 (사람이 고른다 — 후보를 비교해 보이고 선택을 받는다)")
    infra = spec.get("infra") if isinstance(spec.get("infra"), dict) else {}
    for k in ("db", "auth", "hosting"):
        if not infra.get(k):
            out.append(f"infra.{k} 가 비었다 (결정만 기록한다 — 무엇을 쓸지)")
    return out


def current(root, cfg):
    """상태 + 실측 대조 → {phase, label, version, cycle, warnings, next, ...}"""
    st = util.read_state(root)
    warnings = []
    m = design.manifest(root)
    if idx(st["phase"]) > idx("import") and m is None:
        warnings.append("design/ 에 화면이 없다 — 패키지를 다시 가져온다")
        st["phase"] = "import"
    if st.get("locked") and idx(st["phase"]) > idx("review"):
        fp = util.fingerprint(root)
        if fp != st["locked"]["hash"]:
            warnings.append(f"승인 뒤 design/ 또는 api/ 가 바뀌었다 (지문 {st['locked']['hash']} → {fp}) — 재승인이 필요해 ④로 되돌린다")
            st["phase"] = "review"
            st["roles"], st["reports"] = [], []
    if idx(st["phase"]) > idx("spec"):
        probs = spec_problems(util.read_spec(root))
        if probs:
            warnings.append("스펙이 불완전하다: " + "; ".join(probs))
    if st["phase"] == "build":
        for r in st.get("roles", []):
            if not os.path.isdir(util.worktree(root, r)):
                warnings.append(f"워크트리가 없다: {r} — build() 를 다시 부르면 복구한다")
    st["label"] = LABELS[st["phase"]]
    st["human"] = st["phase"] in HUMAN
    st["warnings"] = warnings
    st["next"] = next_action(st)
    st["fingerprint"] = util.fingerprint(root)
    st["manifest"] = m
    return st


def require(st, *phases):
    if st["phase"] in phases:
        return None
    want = " 또는 ".join(LABELS[p] for p in phases)
    return (f"지금은 {st['label']} 단계다. 이 도구는 {want} 에서 쓴다.\n다음: {st['next']}\n"
            "앞 단계로 돌아가려면 back(to=..., reason=...) 을 쓴다.")
