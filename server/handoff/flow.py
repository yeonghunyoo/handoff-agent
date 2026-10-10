"""단계 — 상태 파일 + 실측 대조.

  import → spec → api → review ✋ → build → verify → ship ✋ → done

state.json 은 서버만 쓴다. 그래도 믿지 않는다: 승인(잠금) 뒤 design/ 나 api/ 가 바뀌었으면
지문이 어긋나므로 review 로 강등한다 — 승인은 "그때 그 파일"에만 유효하다.
"""
import os

from . import design, util
from . import infra as infra_mod

PHASES = ["import", "spec", "api", "review", "build", "verify", "ship", "done"]
LABELS = {
    "import": "① 핸드오프 패키지 등록",
    "spec": "② 스펙 · 인프라 결정",
    "api": "③ 백엔드 계약 (openapi.yaml)",
    "review": "④ 계약 확정 — 사람 승인",
    "build": "⑤ 구현 (역할별 워크트리 — 기본 병렬)",
    "verify": "⑥ 검사",
    "ship": "⑦ 완료 승인 — 사람 승인",
    "done": "완료",
}
HUMAN = {"review", "ship"}
WEB_PROJECTS = ("next-app", "vite-react", "existing")   # stack.web_project — v1 은 이 셋 (Svelte/Vue 는 검사 확장자·플레이북이 더 필요)

# stack.backend 선택지 — 서버는 프레임워크를 모른다. 차이는 이 표의 데이터(모드 · 덤프 명령 · 빌더 힌트)뿐이고 분기는 없다.
#   mode  spec-first  계약 api/openapi.yaml 을 읽어 라우팅한다 — 핸들러 없는 operationId 면 서버가 뜨지 않아 이탈이 구조적으로 불가 (기본 추천)
#         generated   코드에서 OpenAPI 를 생성한다 — 덤프를 계약과 집합 diff 로 대조 (빠짐·추가·이름 다름 확정)
#         static      둘 다 아니다 — 경로 리터럴 정규식 추정만 (existing 과 표에 없는 값)
#   dump  구현의 OpenAPI 를 JSON 으로 stdout 에 내는 명령. 빌더가 역할 경로에 그 스크립트를 둔다 (hint 가 내용을 말한다).
BACKEND_STACKS = {
    "connexion": {"lang": "python", "mode": "spec-first", "dump": "python3 openapi_dump.py",
                  "hint": "connexion: app.add_api(<path to api/openapi.yaml>) binds every operationId to a handler by name — the server "
                          "refuses to start while one is missing, so booting is the completeness check. openapi_dump.py prints the loaded "
                          "document as JSON (the API object add_api returns exposes it)."},
    "fastapi": {"lang": "python", "mode": "generated", "dump": "python3 openapi_dump.py",
                "hint": "FastAPI: declare every route with operation_id=<contract operationId> (e.g. @app.get(\"/orders\", operation_id=\"listOrders\")). "
                        "openapi_dump.py: `from main import app; import json; print(json.dumps(app.openapi()))`."},
    "openapi-backend": {"lang": "node", "mode": "spec-first", "dump": "node openapi-dump.mjs",
                        "hint": "openapi-backend: new OpenAPIBackend({definition: <api/openapi.yaml>}) and register(<operationId>, handler) for every "
                                "operation; api.init() validates the definition. openapi-dump.mjs prints JSON.stringify(api.definition)."},
    "fastify-openapi-glue": {"lang": "node", "mode": "spec-first", "dump": "node openapi-dump.mjs",
                             "hint": "fastify-openapi-glue: register with {specification: <api/openapi.yaml>, serviceHandlers} — one method per operationId. "
                                     "openapi-dump.mjs prints the specification you load, as JSON."},
    "fastify": {"lang": "node", "mode": "generated", "dump": "node openapi-dump.mjs",
                "hint": "Fastify + @fastify/swagger: give every route schema an operationId equal to the contract's. openapi-dump.mjs: build the app, "
                        "`await app.ready(); console.log(JSON.stringify(app.swagger()))`."},
    "hono": {"lang": "node", "mode": "generated", "dump": "node openapi-dump.mjs",
             "hint": "Hono + @hono/zod-openapi: createRoute({operationId: <contract operationId>, ...}) for every route. openapi-dump.mjs: "
                     "`console.log(JSON.stringify(app.getOpenAPIDocument({openapi: '3.0.0', info: {title: 'api', version: '1'}})))`."},
    "nestjs": {"lang": "node", "mode": "generated", "dump": "node openapi-dump.mjs",
               "hint": "NestJS + @nestjs/swagger: @ApiOperation({operationId: <contract operationId>}) on every handler. openapi-dump.mjs: "
                       "NestFactory.create(AppModule) then JSON.stringify(SwaggerModule.createDocument(app, new DocumentBuilder().build()))."},
    "existing": {"lang": None, "mode": "static", "dump": "",
                 "hint": "Existing backend: the server can only match route literals statically. If the framework can emit its OpenAPI, still write "
                         "openapi_dump.py / openapi-dump.mjs that prints it as JSON and set verify.schema_dump.backend in .handoff/config.json."},
}
STACK_MODE_KO = {"spec-first": "스펙 우선 (계약이 라우팅 — 이탈 불가)", "generated": "생성 스키마 ↔ 계약 diff", "static": "정적 추정만"}


def backend_stack(spec):
    """spec.stack.backend → 표 항목. 표에 없는 값은 static 으로 본다 (거부하지 않는다 — 기존 스펙과 호환)."""
    stack = spec.get("stack") if isinstance(spec, dict) and isinstance(spec.get("stack"), dict) else {}
    name = str(stack.get("backend") or "").strip().lower()
    return BACKEND_STACKS.get(name) or {"lang": None, "mode": "static", "dump": "", "hint": BACKEND_STACKS["existing"]["hint"]}


def idx(p):
    return PHASES.index(p)


def next_action(st):
    p = st["phase"]
    if p == "import":
        return "import_design(path) — Claude Design 에서 내보낸 zip/폴더 경로를 넘긴다 (레포 루트에 두면 status.candidates 가 찾는다)"
    if p == "spec":
        return ("spec_save(spec) — platforms(ios/android/web) · stack.backend · infra(db/auth/hosting/env) 를 사람에게 묻고 저장한다. "
                "stack.backend 는 선택지로 보인다: 스펙 우선 " + " | ".join(k for k, v in BACKEND_STACKS.items() if v["mode"] == "spec-first")
                + " (추천 — 계약이 라우팅이라 이탈 불가) · 생성형 " + " | ".join(k for k, v in BACKEND_STACKS.items() if v["mode"] == "generated")
                + " (생성 스키마를 계약과 대조) · existing (정적 검사만)")
    if p == "api":
        return "api_submit(openapi) — design/ 의 화면·문서를 읽고 각 화면이 필요로 하는 데이터로 openapi.yaml 을 초안한다"
    if p == "review":
        return "review() — 사람이 계약(design/ + api/ + 스펙)을 승인한다 (elicitation). 요약 표(summary)를 먼저 채팅에 보인다"
    if p == "build":
        if st.get("roles"):
            pending = [r for r in st["roles"] if r not in st.get("reports", [])]
            return f"구현 중 — 리포트 대기: {', '.join(pending) or '없음'} (각 에이전트가 precheck → report)"
        return "build() — 워크트리를 만들고 착수 프롬프트를 받는다. 응답의 dispatch 대로 띄운다 (기본 병렬 — 역할 전부 동시에)"
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
        out.append("platforms 에 ios/android/web 중 하나 이상이 필요하다")
    stack0 = spec.get("stack") if isinstance(spec.get("stack"), dict) else {}
    if "web" in plats and stack0.get("web_project") and stack0["web_project"] not in WEB_PROJECTS:
        out.append(f"stack.web_project '{stack0['web_project']}' 는 지원하지 않는다 ({' | '.join(WEB_PROJECTS)})")
    stack = spec.get("stack") if isinstance(spec.get("stack"), dict) else {}
    if not stack.get("backend"):
        out.append("stack.backend 가 비었다 (사람이 고른다 — 후보를 비교해 보이고 선택을 받는다)")
    infra = spec.get("infra") if isinstance(spec.get("infra"), dict) else {}
    if infra.get("combo") and not infra_mod.combo(infra["combo"]):
        out.append(f"infra.combo '{infra['combo']}' 는 카탈로그에 없다 (status.infra_options 의 id 를 쓰거나 db/auth/hosting 을 직접 적는다)")
    out += infra_mod.pricing_problems(infra.get("pricing"))
    if not infra_mod.scale(infra.get("scale")):
        out.append("infra.scale 가 비었다 (small | medium_plus — 예상 MAU·DAU 를 물어 정한다; infra.mau/dau 숫자를 주면 서버가 정한다)")
    for k in ("db", "auth", "hosting"):
        if not infra.get(k):
            out.append(f"infra.{k} 가 비었다 (결정만 기록한다 — 무엇을 쓸지; status.infra_options 의 조합을 비용과 함께 보이고 고르게 한다)")
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
            st["roles"] = []
    # 접수 목록은 파일에서 derive — report 는 자기 파일만 쓰고 state.json 을 건드리지 않는다 (병렬 빌더의 동시 접수가 안전하다).
    # roles 전부의 리포트가 있으면 phase 는 verify 다 — 결산(verify)은 주행자(advance/submit 또는 verify 호출) 하나가 한 번 한다.
    roles = st.get("roles") or []
    st["reports"] = [r for r in roles if r in util.reported_roles(root, st["version"])]
    if st["phase"] == "build" and roles and len(st["reports"]) == len(roles):
        st["phase"] = "verify"
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
