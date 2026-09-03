"""도구 자신을 검사한다 — 임시 레포 + 가짜 핸드오프 패키지로 전체 사이클을 돈다.

  python3 tests/cases.py            # 전체
  python3 tests/cases.py parity     # 이름 조각으로 좁혀
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from handoff import api, checks, derive, design, flow, gen, git, infra, leaks, score, tools, util  # noqa: E402

APPROVE = lambda m, i: {"approved": True, "reason": ""}          # noqa: E731
REJECT = lambda m, i: {"approved": False, "reason": "빠진 라우트"}  # noqa: E731
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")

OPENAPI = """openapi: 3.0.0
info: {title: Orders, version: "1"}
paths:
  /orders:
    get:
      summary: List orders
    post:
      operationId: createOrder
      summary: Create an order
  /orders/{orderId}:
    get:
      summary: Order detail
"""

SPEC = {"platforms": ["ios", "android"], "stack": {"backend": "fastapi", "ios_project": "xcodegen-spm",
                                                    "android_project": "gradle-modular"},
        "infra": {"scale": "small", "mau": 3000, "dau": 300,
                  "db": "postgres (supabase)", "auth": "supabase auth", "hosting": "fly.io",
                  "env_vars": ["DATABASE_URL", "SUPABASE_KEY"]}}

FAILS = []


def check(name, cond, detail=""):
    print(("  ok  " if cond else "  FAIL") + " " + name + ("" if cond else f"  — {detail}"))
    if not cond:
        FAILS.append(name)


# ─────────────────────────── 픽스처 ───────────────────────────

def make_package(d, screens=("Order List", "Order Detail", "Settings"), tokens=True, readme=True, shots=True,
                 manifest=None, single=False):
    os.makedirs(d, exist_ok=True)
    css = ":root{--color-accent:#0A84FF;--color-bg:#FFFFFF;--spacing-md:16px;--spacing-lg:24px;--radius-pill:999px;--font-body:Inter}"
    if single:
        secs = "".join(f'<section id="{s.lower().replace(" ", "-")}"><h2>{s}</h2></section>' for s in screens)
        with open(os.path.join(d, "index.html"), "w") as f:
            f.write(f"<!doctype html><html><head><title>App</title><style>{css}</style></head><body>{secs}</body></html>")
    else:
        for s in screens:
            fn = s.lower().replace(" ", "-") + ".html"
            with open(os.path.join(d, fn), "w") as f:
                f.write(f"<!doctype html><html><head><title>{s}</title>{'<style>' + css + '</style>' if tokens else ''}"
                        f"</head><body><h1>{s}</h1><button style=\"background:var(--color-accent)\">Go</button></body></html>")
            if shots:
                os.makedirs(os.path.join(d, "screenshots"), exist_ok=True)
                with open(os.path.join(d, "screenshots", fn.replace(".html", ".png")), "wb") as f:
                    f.write(PNG)
    if tokens:
        with open(os.path.join(d, "tokens.json"), "w") as f:
            json.dump({"tokens": {"size": {"controlLg": {"$value": "48px"}}, "color": {"text": {"$value": "#111111"}}}}, f)
    if readme:
        with open(os.path.join(d, "README.md"), "w") as f:
            f.write("# Handoff\n\nTarget stack: SwiftUI + Jetpack Compose, backend FastAPI.\n\nUse tokens for all spacing.\n")
    if manifest:
        with open(os.path.join(d, "manifest.json"), "w") as f:
            json.dump(manifest, f)
    return d


def make_repo():
    root = os.path.join(tempfile.mkdtemp(prefix="handoff-"), "repo")
    os.makedirs(root)
    git.run(root, "init", "-b", "main")
    git.run(root, "config", "user.name", "t")
    git.run(root, "config", "user.email", "t@x")
    git.run(root, "config", "commit.gpgsign", "false")
    tools.setup(root)
    with open(os.path.join(root, "README.md"), "w") as f:
        f.write("# app\n")
    git.run(root, "add", "-A")
    git.run(root, "commit", "-q", "-m", "init")
    return root


def to_locked(root, pkg=None, spec=None, openapi=OPENAPI):
    pkg = pkg or make_package(os.path.join(os.path.dirname(root), "pkg"))
    r = tools.import_design(root, pkg)
    assert r["ok"], r["message"]
    r = tools.spec_save(root, spec or SPEC)
    assert r["ok"] and not r.get("draft"), r["message"]
    r = tools.api_submit(root, openapi)
    assert r["ok"], r["message"]
    r = tools.review(root, approver=APPROVE)
    assert r["ok"] and r["approved"], r["message"]
    return root


def wt(root, role):
    return util.worktree(root, role)


def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def implement(root, roles=("backend", "ios", "android"), hardcode=False, skip_screen=None, tests=True):
    for role in roles:
        t = wt(root, role)
        if role == "backend":
            w(os.path.join(t, "backend", "app.py"),
              '@app.get("/orders")\ndef list_orders(): ...\n@app.post("/orders")\ndef create(): ...\n'
              '@app.get("/orders/{order_id}")\ndef detail(order_id): ...\n')
            if tests:
                w(os.path.join(t, "backend", "tests", "test_api.py"),
                  'def test_list():\n    assert client.get("/orders").status_code == 200\n')
        else:
            ext = "swift" if role == "ios" else "kt"
            screens = ["orderList", "orderDetail", "settings"]
            if skip_screen and role == skip_screen[0]:
                screens = [s for s in screens if s != skip_screen[1]]
            body = "\n".join(f"let s{i} = Screens.{s}" for i, s in enumerate(screens))
            body += "\nlet a = ApiRoutes.getOrders; let b = ApiRoutes.createOrder; let c = ApiRoutes.getOrdersByOrderId"
            body += "\nlet p = DesignTokens.Spacing.md; let q = DesignTokens.Color.accent"
            if hardcode and role == "ios":
                body += '\nlet bad = "#0A84FF"\nview.padding(16)'
            w(os.path.join(t, f"apps/{role}", f"Main.{ext}"), body + "\n")
            if tests:
                w(os.path.join(t, f"apps/{role}", f"MainTests.{ext}"), "func testA() { XCTAssertEqual(Screens.orderList, \"orderList\") }\n")
        git.commit_all(t, f"{role}: impl")


def report(status="done", **kw):
    return {"status": status, "not_done": [], "blocked": [], "divergences": [], "proposals": [],
            "build": {"ok": True, "seconds": 3}, "tests": {"passed": 4, "failed": 0, "seconds": 2},
            "human_check": [], **kw}


# ─────────────────────────── 테스트 ───────────────────────────

def test_yaml_and_routes():
    doc = api.loads(OPENAPI)
    rs = api.routes(doc)
    check("yaml: 라우트 3개", [r["name"] for r in rs] == ["getOrders", "createOrder", "getOrdersByOrderId"], rs)
    check("yaml: operationId 우선", rs[1]["method"] == "POST" and rs[1]["summary"] == "Create an order")
    check("yaml: json 도 받는다", api.routes(api.loads(json.dumps({"paths": {"/a": {"get": {}}}})))[0]["name"] == "getA")
    for bad, why in (("paths: {}", "paths 비었다"), ("paths:\n  orders:\n    get: {}", "슬래시 없음"),
                     ("paths:\n  /a:\n    get: {operationId: x}\n  /b:\n    get: {operationId: x}", "이름 겹침")):
        try:
            api.validate(bad)
            check(f"yaml: 거부 — {why}", False)
        except api.ApiError:
            check(f"yaml: 거부 — {why}", True)
    rx = api.path_regex("/orders/{orderId}")
    check("path_regex: 파라미터 표기 무관", bool(rx.search('"/orders/:id"')) and bool(rx.search("'/orders/{order_id}'"))
          and not api.path_regex("/orders").search('"/orders/{id}"'))


def test_design_scan():
    base = tempfile.mkdtemp(prefix="pkg-")
    root = make_repo()
    pkg = make_package(os.path.join(base, "a"))
    r = tools.import_design(root, pkg)
    check("scan: 화면 3", [s["id"] for s in r["screens"]] == ["orderDetail", "orderList", "settings"], r["screens"])
    m = design.scan(root)
    check("scan: 제목은 <title>", m["screens"][1]["title"] == "Order List")
    check("scan: 토큰 css+json 병합", m["tokens"]["color.accent"] == {"kind": "color", "value": "#0A84FF"}
          and m["tokens"]["spacing.md"]["value"] == 16 and m["tokens"]["size.controlLg"]["value"] == 48
          and m["tokens"]["color.text"]["value"] == "#111111" and m["tokens"]["font.body"]["kind"] == "other", m["tokens"])
    check("scan: 스크린샷 매칭", m["screens"][1]["shots"] == ["screenshots/order-list.png"], m["screens"])
    check("scan: 문서", m["docs"] == ["README.md"])
    check("scan: 힌트", any("FastAPI" in h for h in r["hints"]))
    # zip
    zp = os.path.join(base, "pkg.zip")
    with zipfile.ZipFile(zp, "w") as z:
        for d, _, ns in os.walk(pkg):
            for n in ns:
                p = os.path.join(d, n)
                z.write(p, os.path.join("wrapper", os.path.relpath(p, pkg)))
        z.writestr("__MACOSX/._x", "junk")
    r = tools.import_design(root, zp)
    check("scan: zip + 겉폴더 한 겹", r["ok"] and len(r["screens"]) == 3 and os.path.isfile(os.path.join(root, "design", "README.md")), r.get("message"))
    # 단일 html 섹션
    single = make_package(os.path.join(base, "s"), single=True, shots=False)
    r = tools.import_design(root, single)
    check("scan: 단일 html 의 섹션이 화면", [s["id"] for s in r["screens"]] == ["orderList", "orderDetail", "settings"]
          and r["screens"][0]["file"] == "index.html", r["screens"])
    # 매니페스트 우선
    mp = make_package(os.path.join(base, "m"), manifest={"screens": [{"id": "home", "file": "order-list.html", "title": "홈"}],
                                                          "tokens": {"color": {"brand": "#FF0000"}}})
    r = tools.import_design(root, mp)
    check("scan: 패키지 매니페스트 우선", [s["id"] for s in r["screens"]] == ["home"] and design.scan(root)["tokens"]["color.brand"]["value"] == "#FF0000", r["screens"])
    # 화면 없음
    empty = os.path.join(base, "e")
    os.makedirs(empty)
    w(os.path.join(empty, "README.md"), "x")
    r = tools.import_design(root, empty)
    check("scan: 화면 0 거부", not r["ok"] and "화면" in r["message"], r["message"])
    check("scan: 실패한 가져오기가 이전 design/ 를 지운다 (원본 없음 상태)", flow.current(root, util.load_config(root))["phase"] == "import")


def make_bundle(path, states=("isOnboarding", "isHome", "isStats")):
    """Claude Design 'standalone HTML' 모양 — 매니페스트(gzip+base64) + 템플릿(아트보드) + CDN 목록."""
    import gzip
    def enc(b):
        return base64.b64encode(gzip.compress(b)).decode()
    jsx_id, rt_id, font_id, react_id = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222", \
        "33333333-3333-4333-8333-333333333333", "44444444-4444-4444-8444-444444444444"
    man = {jsx_id: {"mime": "text/jsx", "compressed": True, "data": enc(b"// frame\nfunction IOSDevice(){}")},
           rt_id: {"mime": "text/javascript", "compressed": True, "data": enc(b"// GENERATED from dc-runtime/src/*.ts")},
           font_id: {"mime": "font/woff2", "compressed": True, "data": enc(b"wOF2fake")},
           react_id: {"mime": "text/javascript", "compressed": True, "data": enc(b"react")}}
    body = "".join(f'<sc-if value="{{{{ {st} }}}}"><div>{st}</div></sc-if>' for st in states)
    tpl = (f'<!DOCTYPE html><html><head><script src="{rt_id}"></script></head><body><x-dc><helmet><style>'
           f':root{{--color-accent:#c67139;--space-4:17.6px}} @font-face{{src:url("{font_id}")}}</style></helmet>'
           f'<x-import from="{jsx_id}#/ios-frame.jsx">{body}</x-import></x-dc></body></html>')
    html = ('<!DOCTYPE html><html><head><title>Bundled Page</title></head><body>'
            f'<script type="__bundler/manifest">{json.dumps(man)}</script>'
            '<script type="__bundler/ext_resources">' + json.dumps([{"id": "https://unpkg.com/react@18/umd/react.production.min.js", "uuid": react_id}]) + '</script>'
            f'<script type="__bundler/template">{json.dumps(tpl).replace("</", "<\\/")}</script></body></html>')   # 실제 내보내기처럼 </ 를 이스케이프
    with open(path, "w") as f:
        f.write(html)
    return path


def test_bundle_and_states():
    root = make_repo()
    b = make_bundle(os.path.join(os.path.dirname(root), "forest.html"))
    r = tools.import_design(root, b)
    check("bundle: 펼침 — 아트보드·jsx·런타임·폰트, react 는 제외",
          os.path.isfile(os.path.join(root, "design/forest.dc.html")) and os.path.isfile(os.path.join(root, "design/ios-frame.jsx"))
          and os.path.isfile(os.path.join(root, "design/support.js")) and os.path.isdir(os.path.join(root, "design/fonts"))
          and not any("react" in n for n in os.listdir(os.path.join(root, "design"))), os.listdir(os.path.join(root, "design")))
    tpl = open(os.path.join(root, "design/forest.dc.html")).read()
    check("bundle: uuid 참조를 경로로 되돌림", 'from="./ios-frame.jsx"' in tpl and 'src="./support.js"' in tpl and "1111-4111" not in tpl, tpl[:300])
    check("bundle: 상태 분기 → 화면 후보 3", [s["id"] for s in r["screens"]] == ["onboarding", "home", "stats"] and not r["confirmed"], r["screens"])
    check("bundle: 확정 요청 경고", "확정받은" in r["message"])
    check("bundle: 토큰은 인라인 css 에서", r["tokens"] == 2)
    r = tools.import_design(root, None, screens=[{"id": "onboarding", "title": "온보딩", "file": "forest.dc.html", "anchor": "isOnboarding"},
                                                {"id": "home", "title": "홈", "file": "forest.dc.html", "anchor": "isHome"},
                                                {"id": "nowPlaying", "title": "재생 중", "file": "forest.dc.html", "anchor": "nowPlayingOpen"}])
    m = design.scan(root)
    check("bundle: 사람 확정 목록이 우선", r["confirmed"] and [s["id"] for s in m["screens"]] == ["onboarding", "home", "nowPlaying"]
          and m["screens"][2]["anchor"] == "nowPlayingOpen" and os.path.isfile(os.path.join(root, "design", design.MANIFEST_NAME)), m["screens"])
    check("bundle: 응답에 컴포넌트·내비", isinstance(r["components"], list) and "entry" in r["navigation"] and "컴포넌트" in r["message"], r["message"])
    # CLI 로 펼치기만
    out = os.path.join(os.path.dirname(root), "unb")
    r_ = subprocess.run([sys.executable, os.path.join(HERE, "..", "server", "run.py"), "unbundle", b, out], capture_output=True, text=True)
    check("bundle: run.py unbundle", r_.returncode == 0 and os.path.isfile(os.path.join(out, "forest.dc.html")) and "ios-frame.jsx" in r_.stdout, r_.stdout + r_.stderr)
    check("bundle: run.py unbundle 은 번들 아닌 html 거부",
          subprocess.run([sys.executable, os.path.join(HERE, "..", "server", "run.py"), "unbundle", os.path.join(root, "README.md"), out], capture_output=True, text=True).returncode == 1)
    # 탐색 보드는 화면이 아니다
    w(os.path.join(root, "design/Home Options.dc.html"), '<html><head><meta name="design_doc_mode" content="canvas"></head><body>'
      + "".join(f'<x-import from="./ios-frame.jsx"><div id="{i}"></div></x-import>' for i in ("1a", "1b", "1c")) + "</body></html>")
    m = design.scan(root)
    check("bundle: 캔버스 보드 분리", m["boards"] == ["Home Options.dc.html"] and [s["id"] for s in m["screens"]] == ["onboarding", "home", "nowPlaying"], (m["boards"], m["screens"]))
    # 잠긴 뒤 프롬프트에 anchor 가 실린다
    tools.spec_save(root, SPEC); tools.api_submit(root, OPENAPI); tools.review(root, approver=APPROVE)
    r = tools.build(root)
    check("bundle: 프롬프트에 화면 anchor·보드", "forest.dc.html#isHome" in r["prompts"]["ios"] and "Screens.nowPlaying" in r["prompts"]["ios"], r["prompts"]["ios"][:1200])


PROTO = """<!DOCTYPE html><html><head><script src="./support.js"></script></head><body><x-dc><helmet>
<style>:root{--color-accent:#c67139;--space-4:17.6px;--font-body:"Figtree"}</style></helmet>
<x-import component-from-global-scope="IOSDevice" from="./ios-frame.jsx" hint-size="402px,874px">
<sc-if value="{{ isOnboarding }}" hint-placeholder-val="{{ true }}"><div><span sc-camel-on-click="{{ skipOnboarding }}">건너뛰기</span>
<h1>{{ obSlide.title }}</h1><div sc-camel-on-click="{{ nextOnboarding }}">{{ obButtonLabel }}</div></div></sc-if>
<sc-if value="{{ appVisible }}" hint-placeholder-val="{{ false }}"><div>
<sc-if value="{{ isHome }}" hint-placeholder-val="{{ true }}"><div><span>forest</span><span>추천 믹싱</span>
<div sc-camel-on-click="{{ p.open }}"><svg width="14" viewBox="0 0 24 24"><path d="M7 4.5v15z"></path></svg></div></div></sc-if>
<sc-if value="{{ isStats }}" hint-placeholder-val="{{ false }}"><h1>기록</h1></sc-if>
<div sc-camel-on-click="{{ setTabHome }}"><svg width="22" viewBox="0 0 24 24"><path d="M3 11l9-8z"></path></svg>홈</div>
<div sc-camel-on-click="{{ setTabStats }}"><svg width="22" viewBox="0 0 24 24"><path d="M4 20V10z"></path></svg>기록</div>
</div></sc-if>
<div style="{{ settingsSheetStyle }}"><b>김서연</b><span>구독 관리</span>
<span sc-camel-on-click="{{ closeSettings }}"><svg width="16" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"></path></svg></span></div>
<div style="{{ settingsSheetStyle }}"><span>로그아웃</span><svg width="14" viewBox="0 0 24 24"><path d="M7 4.5v15z"></path></svg></div>
</x-import></x-dc>
<script type="text/x-dc" data-dc-script>
const LAYERS = [
  { id: 'wave', label: '파도', color: 'var(--color-accent-500)' },
  { id: 'rain', label: '비', color: 'var(--color-accent-2-600)' },
];
const PRESETS = [
  { id: 'evening-wave', name: '저녁 물결', desc: '파도 · 장작', volumes: { wave: 75, fire: 45 } },
];
const SLEEP_OPTS = [15, 30, null];
class Component extends DCLogic {
  state = { isOnboarding: true, tab: 'home', settingsOpen: false, favorites: ['evening-wave'], playElapsed: 0 };
  skipOnboarding = () => this.setState({ isOnboarding: false, tab: 'home' });
  nextOnboarding = () => { this.setState(s => ({ obStep: s.obStep + 1 })); };
  openSettings = () => this.setState({ settingsOpen: true });
  closeSettings = () => this.setState({ settingsOpen: false });
  startPress = () => { this._lp = setTimeout(() => this.setState(s => ({ uiHidden: !s.uiHidden })), 550); };
  renderVals() {
    const label = `${String(1).padStart(2, '0')} 재생 중`;
    return { setTabHome: () => this.setTab('home'), setTabStats: () => this.setTab('stats'),
             obButtonLabel: s.obStep === 2 ? '시작하기' : '다음' };
  }
}
</script></body></html>
"""

CHAT = ('{"chats":{"a":{"title":"t","messages":[{"role":"user","content":"Continuing from x"},'
        '{"role":"assistant","content":"ok"},{"role":"user","content":"온보딩 건너뛰기는 홈으로"},'
        '{"role":"user","content":"1a 채택"}]}}}')


def test_derive():
    root = make_repo()
    d = os.path.join(root, "design")
    w(os.path.join(d, "forest.dc.html"), PROTO)
    w(os.path.join(d, "chats", "conversation.json"), CHAT)
    w(os.path.join(d, "_ds", "org", "_adherence.oxlintrc.json"), json.dumps({"rules": {"no-restricted-syntax": ["warn",
        {"selector": "Literal[value=/#[0-9a-fA-F]{3,8}\\b/]", "message": "Raw hex color"}, {"selector": "x", "message": "Raw px value"}]},
        "x-omelette": {"fontFamilies": ["Caprasimo", "Figtree"]}}))
    r = tools.import_design(root, "design", screens=[
        {"id": "onboarding", "title": "온보딩", "file": "forest.dc.html", "anchor": "isOnboarding"},
        {"id": "home", "title": "홈", "file": "forest.dc.html", "anchor": "isHome"},
        {"id": "stats", "title": "기록", "file": "forest.dc.html", "anchor": "isStats"},
        {"id": "settings", "title": "설정 시트", "file": "forest.dc.html", "anchor": "settingsOpen"}])
    dv = r["derived"]
    check("derive: 요약", dv["entities"] == ["LAYERS", "PRESETS", "SLEEP_OPTS"] and dv["intent_turns"] == 2 and dv["rules"]["fonts"] == ["Caprasimo", "Figtree"]
          and dv["rules"]["no_hex"] and dv["rules"]["no_px"], dv)
    e = derive.read(root, "entities.json")
    check("derive: JS 리터럴 → JSON", e["PRESETS"][0]["volumes"] == {"wave": 75, "fire": 45} and e["SLEEP_OPTS"] == [15, 30, None]
          and e["_state"]["favorites"] == ["evening-wave"] and e["_state"]["tab"] == "home", e)
    st = {x["key"]: x for x in derive.read(root, "strings.json")["strings"]}
    check("derive: 문구 — 화면 귀속 · 로마자 키 · 데이터 문구 · 템플릿 조각",
          st["onboarding.geonneottwigi"]["text"] == "건너뛰기" and st["home.chucheonMiksing"]["screen"] == "home"
          and st["settings.gimseoyeon"]["screen"] == "settings" and st["presets.eveningWave.name"]["text"] == "저녁 물결"
          and st["layers.wave.label"]["text"] == "파도" and any(v["text"] == "{} 재생 중" for v in st.values())
          and any(v["text"] == "시작하기" for v in st.values()), sorted(st)[:20])
    check("derive: {{ }} 바인딩은 문구가 아니다", not any("obSlide" in k or "{{" in v["text"] for k, v in st.items()))
    ic = {i["name"]: i for i in derive.read(root, "icons.json")["icons"]}
    check("derive: 아이콘 — 핸들러 이름 · 중복 제거 · 화면", set(ic) == {"play", "tabHome", "tabStats", "chevronDown"}
          and ic["play"]["uses"] == 2 and set(ic["play"]["screens"]) == {"home", "settings"}, ic)
    check("derive: 아이콘 svg 파일", os.path.isfile(os.path.join(d, "derived", "icons", "tabHome.svg")))
    b = derive.read(root, "behavior.json")
    check("derive: 전이표", b["tab_transitions"] == {"setTabHome": {"tab": "home"}, "setTabStats": {"tab": "stats"}}
          and b["handlers"]["skipOnboarding"]["sets"] == ["isOnboarding", "tab"] and b["timers_ms"] == [550], b)
    intent = open(os.path.join(d, "derived", "intent.md"), encoding="utf-8").read()
    check("derive: 의도 로그 (Continuing 제외)", "온보딩 건너뛰기는 홈으로" in intent and "1a 채택" in intent and "Continuing" not in intent)
    m = design.scan(root)
    check("derive: derived/ 는 문서·화면으로 안 센다", m["docs"] == [] and len(m["screens"]) == 4 and m["chats"] == ["chats/conversation.json"], (m["docs"], m["chats"]))
    # 컴포넌트 — 타입 분류 · 화면 귀속 (설정 시트는 사람이 화면으로 승격했으니 오버레이 컴포넌트가 아니다)
    cj = {c["id"]: c for c in derive.read(root, "components.json")["components"]}
    check("derive: 컴포넌트 타입·귀속", cj["skipOnboarding"]["type"] == "button" and cj["skipOnboarding"]["title"] == "건너뛰기"
          and cj["skipOnboarding"]["screen"] == "onboarding" and cj["setTabHome"]["type"] == "tab" and cj["setTabHome"]["target"] == "home"
          and cj["closeSettings"]["screen"] == "settings" and cj["closeSettings"]["icon"] is True
          and not any(c["type"] == "sheet" for c in cj.values()) and dv["components"] == {"button": 3, "tab": 2}, cj)
    ov = derive.components(PROTO, [{"id": "onboarding", "anchor": "isOnboarding"}, {"id": "home", "anchor": "isHome"}])
    sh = next((c for c in ov if c["type"] == "sheet"), None)
    check("derive: 오버레이 → sheet 컴포넌트 (열고 닫는 핸들러 · anchor · 안의 버튼 귀속)",
          sh and sh["id"] == "settingsSheet" and sh["anchor"] == "settingsOpen" and sh["open"] == ["openSettings"] and sh["close"] == ["closeSettings"]
          and next(c["screen"] for c in ov if c["id"] == "closeSettings") == "settingsSheet", ov)
    nav = derive.read(root, "navigation.json")
    check("derive: 내비게이션 — 진입 · 탭 · 전이", nav["entry"] == "onboarding" and nav["tabs"] == {"setTabHome": "home", "setTabStats": "stats"}
          and {"via": "skipOnboarding", "to": "home", "action": "go"} in nav["transitions"]
          and {"via": "openSettings", "to": "settings", "action": "go"} not in nav["transitions"], nav)
    man = json.load(open(os.path.join(d, design.MANIFEST_NAME), encoding="utf-8"))
    check("derive: 매니페스트 v2 상세 — 화면별 컴포넌트·문구·아이콘 · 내비 · state · 모델 · 요약",
          man["version"] == 2 and man["navigation"]["entry"] == "onboarding" and "skipOnboarding" in man["screens"][0]["components"]
          and "onboarding.geonneottwigi" in man["screens"][0]["strings"] and "tabHome" in man["screens"][1]["icons"] + man["screens"][0]["icons"] + man.get("icons", {}).get("names", [])
          and man["state"]["tab"] == "home" and man["entities"]["PRESETS"] == {"count": 1, "fields": ["desc", "id", "name", "volumes"]}
          and man["strings"]["count"] == dv["strings"] and man["tokens"]["count"] == 3 and man["components_confirmed"] is False
          and man["component_types"] == {"button": 3, "tab": 2}, man)
    # 사람이 컴포넌트를 확정 — 준 것은 이름·타입이 이기고 나머지는 서버 추출이 채운다
    r2 = tools.import_design(root, None, components=[{"id": "skipOnboarding", "type": "button", "title": "건너뛰기 버튼"}])
    man = json.load(open(os.path.join(d, design.MANIFEST_NAME), encoding="utf-8"))
    c0 = {c["id"]: c for c in man["components"]}
    check("derive: 컴포넌트 확정 병합", r2["ok"] and man["components_confirmed"] is True and c0["skipOnboarding"]["title"] == "건너뛰기 버튼"
          and c0["skipOnboarding"]["screen"] == "onboarding" and "setTabHome" in c0 and [s["id"] for s in man["screens"]] == ["onboarding", "home", "stats", "settings"],
          (r2.get("message"), list(c0)))
    # 생성 상수 + 검사
    tools.spec_save(root, SPEC); tools.api_submit(root, OPENAPI); tools.review(root, approver=APPROVE)
    r = tools.build(root)
    gen_dir = os.path.join(wt(root, "ios"), "shared", "generated")
    check("derive: Strings/Icons 생성", os.path.isfile(os.path.join(gen_dir, "Strings.swift")) and os.path.isfile(os.path.join(gen_dir, "Icons.kt")))
    sw = open(os.path.join(gen_dir, "Strings.swift")).read()
    check("derive: Strings 내용", 'public enum Onboarding {' in sw and 'geonneottwigi = "건너뛰기"' in sw and "public enum Presets {" in sw, sw[:600])
    p = r["prompts"]["ios"]
    check("derive: 프롬프트 — intent 먼저 · ICN 항목 · Strings/Icons", "design/derived/intent.md" in p and "ICN-01" in p and "Icons.tabHome" in p
          and "Strings.swift, Icons.swift" in p, p[:1500])
    check("derive: 프롬프트 — rules.json (앱만)", "design/derived/rules.json" in p and "design/derived/rules.json" not in r["prompts"]["backend"], p[:1500])
    check("derive: 프롬프트 — 컴포넌트(타입) 목록 · 매니페스트", "## Components" in p and "tab      `setTabHome` — 홈 (in shared; handler setTabHome; → home)" in p
          and "design/handoff.manifest.json" in p and "button   `skipOnboarding` — 건너뛰기 버튼" in p, p)
    check("derive: 대시보드 — 컴포넌트 표", "컴포넌트 — 5개 (사람 확정)" in open(os.path.join(root, "docs", "handoff-dashboard.html"), encoding="utf-8").read())
    # 검사: 한글 리터럴 = raw-string, 미허용 폰트 = raw-font, 아이콘 소비
    t = wt(root, "ios")
    w(os.path.join(t, "apps/ios/Main.swift"), 'let a = Strings.Onboarding.geonneottwigi\nlet b = Text("건너뛰기")\nlet f = Font.custom("Inter", size: 12)\n'
      'let i = Icons.tabHome\nlet s = Screens.home; let r = ApiRoutes.getOrders\n')
    git.commit_all(t, "impl")
    pre = tools.precheck(root, "ios")
    kinds = sorted(h["kind"] for h in pre["hardcodes"])
    check("derive: raw-string · raw-font 검출", kinds == ["raw-font", "raw-string"], pre["hardcodes"])
    check("derive: 아이콘 소비 항목", any(u.startswith("ICN-") for u in pre["consumption"]["unused"]) and pre["consumption"]["total"] == 3 + 4 + 4, pre["consumption"])


def test_flow_gates():
    root = make_repo()
    st = flow.current(root, util.load_config(root))
    check("flow: 시작은 import", st["phase"] == "import")
    for name, r in (("spec 전 spec_save", tools.spec_save(root, SPEC)), ("api 전 api_submit", tools.api_submit(root, OPENAPI)),
                    ("review 전 review", tools.review(root, approver=APPROVE)), ("build 전 build", tools.build(root)),
                    ("verify 전 verify", tools.verify(root)), ("ship 전 ship", tools.ship(root, approver=APPROVE))):
        check(f"flow: 거부 — {name}", not r["ok"] and "단계" in r["message"], r["message"])
    pkg = make_package(os.path.join(os.path.dirname(root), "pkg"))
    tools.import_design(root, pkg)
    r = tools.spec_save(root, {"platforms": ["ios"]})
    check("flow: 스펙 부분 저장", r.get("draft") and any("stack.backend" in p for p in r["remaining"]), r)
    r = tools.spec_save(root, {"stack": {"backend": "fastapi"}, "infra": {"scale": "small", "db": "pg", "auth": "jwt", "hosting": "fly"}})
    check("flow: 누적 후 확정", r["ok"] and not r.get("draft") and r["spec"]["platforms"] == ["ios"], r)
    r = tools.api_submit(root, "nope: 1")
    check("flow: 깨진 openapi 거부", not r["ok"] and not os.path.exists(util.api_path(root)))
    r = tools.api_submit(root, 'paths:\n  /x:\n    get:\n      description: "api_key = \\"sk_live_ABCDEFGHIJKLMNOPQRST\\""')
    check("flow: 시크릿 든 openapi 거부", not r["ok"] and "시크릿" in r["message"])
    tools.api_submit(root, OPENAPI)
    r = tools.review(root, approver=None)
    check("flow: approver 없으면 pending", not r["ok"] and r["pending_human"] and "지문" in r["approval_prompt"])
    r = tools.review(root, approver=REJECT)
    check("flow: 반려 → review 에 머문다 (고치고 다시)", r["ok"] and not r["approved"] and flow.current(root, util.load_config(root))["phase"] == "review")
    check("flow: review 에서 api 재제출 허용", tools.api_submit(root, OPENAPI)["ok"])
    r = tools.review(root, approver=APPROVE)
    st = flow.current(root, util.load_config(root))
    check("flow: 승인 → build · v1 · 잠금", r["approved"] and st["phase"] == "build" and st["version"] == 1 and st["locked"]["hash"] == st["fingerprint"])
    check("flow: design/ api/ 가 본선 커밋", "design/order-list.html" in git.run(root, "ls-files").stdout and "api/openapi.yaml" in git.run(root, "ls-files").stdout)
    # 잠금 뒤 변조
    w(util.api_path(root), OPENAPI + "  /extra:\n    get: {}\n")
    st = flow.current(root, util.load_config(root))
    check("flow: 잠금 뒤 api 변조 → review 강등", st["phase"] == "review" and any("지문" in x for x in st["warnings"]), st["warnings"])
    r = tools.build(root)
    check("flow: 강등 뒤 build 거부", not r["ok"])
    tools.review(root, approver=APPROVE)
    check("flow: 재승인 v2", flow.current(root, util.load_config(root))["version"] == 2)
    r = tools.back(root, "spec", "인프라 바꿈")
    check("flow: back → spec", r["ok"] and flow.current(root, util.load_config(root))["phase"] == "spec")
    check("flow: back 은 앞으로 못 감", not tools.back(root, "build", "x")["ok"])


def test_infra():
    check("infra: scale_of 경계", infra.scale_of(3000, 300) == "small" and infra.scale_of(10_000, 1_000) == "small"
          and infra.scale_of(10_001, 10) == "medium_plus" and infra.scale_of(100, 5000) == "medium_plus")
    check("infra: scale_of 표기", infra.scale_of("12,000", None) == "medium_plus" and infra.scale_of("3k", "0.2k") == "small"
          and infra.scale_of(None, None) is None and infra.scale_of("많음", None) is None)
    ids = [c["id"] for c in infra.COMBOS]
    check("infra: 조합 카탈로그", len(ids) >= 10 and len(set(ids)) == len(ids)
          and all(set(c["cost"]) == {"small", "medium_plus"} and c["db"] and c["auth"] and c["hosting"] and c["fit"] for c in infra.COMBOS))
    cat = infra.catalog()
    check("infra: 규모 없으면 조합도 요금 페이지도 없다 — 규모부터", cat["scale"] is None and cat["combos"] == [] and cat["services"] == {}
          and "규모" in cat["note"] and len(cat["scales"]) == 2)
    cat = infra.catalog("medium_plus")
    lows = [infra._cost_low(c["cost"]) for c in cat["combos"]]
    check("infra: 규모별 후보 4~5개 — 싼 순 · 나머지는 others", 4 <= len(cat["combos"]) <= 5 and lows == sorted(lows)
          and isinstance(cat["combos"][0]["cost"], str) and all(c["recommended"] for c in cat["combos"])
          and any(c["id"] == "aws-classic" for c in cat["combos"])
          and {o["id"] for o in cat["others"]} | {c["id"] for c in cat["combos"]} == set(ids)
          and set(cat["services"]) == {n for c in cat["combos"] for n in c["services"]} and len(cat["services"]) < len(infra.SERVICES),
          cat["combos"][:3])
    check("infra: 소규모 후보는 다르다", [c["id"] for c in infra.catalog("small")["combos"]] != [c["id"] for c in cat["combos"]]
          and all(c["id"] in infra.SHORTLIST["small"] for c in infra.catalog("small")["combos"]))
    root = make_repo()
    tools.import_design(root, make_package(os.path.join(os.path.dirname(root), "pkg")))
    s = tools.status(root)
    check("infra: status — 규모 전엔 후보 없이 규모부터 경고", s["infra_options"] and s["infra_options"]["scale"] is None
          and s["infra_options"]["combos"] == [] and any("규모부터" in w for w in s["warnings"]) and not any("요금이 아직" in w for w in s["warnings"]), s["warnings"])
    r = tools.spec_save(root, {"platforms": ["ios"], "stack": {"backend": "fastapi"}, "infra": {"mau": 50000, "dau": 4000}})
    check("infra: mau/dau → scale", r["draft"] and r["draft_spec"]["infra"]["scale"] == "medium_plus"
          and not any("infra.scale" in p for p in r["remaining"]) and r["infra_options"]["scale"] == "medium_plus", r)
    s = tools.status(root)
    check("infra: status 카탈로그가 규모를 따른다", s["infra_options"]["scale"] == "medium_plus" and s["infra_options"]["combos"][0]["recommended"]
          and 4 <= len(s["infra_options"]["combos"]) <= 5 and any("요금이 아직" in w and "개만" in w for w in s["warnings"]), s["warnings"])
    r = tools.spec_save(root, {"infra": {"combo": "nope"}})
    check("infra: 없는 combo 거부", r["draft"] and any("infra.combo" in p for p in r["remaining"]) and r["draft_spec"]["infra"]["mau"] == 50000, r)
    r = tools.spec_save(root, {"infra": {"combo": "supabase-fly", "db": "PostgreSQL (Neon)"}})
    i = r["spec"]["infra"]
    check("infra: combo 펼침 — 사람 값은 안 덮는다", r["ok"] and not r.get("draft") and i["db"] == "PostgreSQL (Neon)"
          and i["auth"] == "Supabase Auth" and i["hosting"] == "Fly.io" and i["cost"] == "$60–300" and i["scale"] == "medium_plus"
          and i["mau"] == 50000, i)
    check("infra: env_vars 만 따로 저장해도 누적", tools.spec_save(root, {"infra": {"env_vars": ["DATABASE_URL"]}})["spec"]["infra"]["db"] == "PostgreSQL (Neon)")
    check("infra: 폴백으로 고르면 표시", i.get("cost_basis") == infra.COST_BASIS and not i.get("cost_checked") and i["cost_sources"], i)
    # 요금은 매번 새로 — pricing 저장 전엔 경고 + 폴백, 저장 후엔 확인일·출처
    root = make_repo()
    tools.import_design(root, make_package(os.path.join(os.path.dirname(root), "pkg")))
    tools.spec_save(root, {"infra": {"mau": 200, "dau": 20}})   # 규모 먼저
    s = tools.status(root)
    check("pricing: 조회 전 경고", any("요금" in w for w in s["warnings"]) and s["infra_options"]["fresh"] == 0
          and s["infra_options"]["stale"] == len(s["infra_options"]["combos"]) == len(infra.SHORTLIST["small"])
          and s["infra_options"]["services"]["supabase"].startswith("https://")
          and all(r.get("cost_basis") == infra.COST_BASIS and r["sources"] for r in s["infra_options"]["combos"]), s["warnings"])
    bad = tools.spec_save(root, {"platforms": ["ios"], "infra": {"pricing": {"checked": "어제", "combos": {"nope": {"small": "$1"}}}}})
    check("pricing: 모양 검사", bad["draft"] and sum("infra.pricing" in p for p in bad["remaining"]) == 2, bad["remaining"])
    pr = {"checked": "2026-09-03", "combos": {"supabase-fly": {"small": "$0–25", "medium_plus": "$75–350",
                                                                "sources": ["https://supabase.com/pricing", "https://fly.io/pricing/"], "note": "compute-hours"}}}
    r = tools.spec_save(root, {"infra": {"pricing": pr, "mau": 200, "dau": 20}})
    rows = {c["id"]: c for c in r["infra_options"]["combos"]}
    check("pricing: 저장 후 표에 반영", not any("infra.pricing" in p for p in r["remaining"]) and r["infra_options"]["fresh"] == 1
          and rows["supabase-fly"]["cost"] == "$0–25" and rows["supabase-fly"]["cost_checked"] == "2026-09-03"
          and rows["supabase-fly"]["cost_note"] == "compute-hours" and rows["railway"].get("cost_basis") == infra.COST_BASIS, rows["supabase-fly"])
    s = tools.status(root)
    check("pricing: 경고 해제", not any("요금" in w for w in s["warnings"]) and s["infra_options"]["cost_checked"] == "2026-09-03", s["warnings"])
    r = tools.spec_save(root, {"stack": {"backend": "fastapi"}, "infra": {"combo": "supabase-fly"}})
    i = r["spec"]["infra"]
    check("pricing: 고른 조합의 비용은 조회값", r["ok"] and i["cost"] == "$0–25" and i["cost_checked"] == "2026-09-03"
          and i["cost_sources"] == pr["combos"]["supabase-fly"]["sources"] and "cost_basis" not in i, i)
    tools.api_submit(root, OPENAPI); tools.review(root, approver=APPROVE)
    r = tools.build(root)
    check("pricing: 착수 프롬프트에 확인일", "$0–25 (checked 2026-09-03)" in r["prompts"]["backend"], r["prompts"]["backend"][:1500])
    plan = open(os.path.join(root, "docs", "handoff-plan.md"), encoding="utf-8").read()
    dash = open(os.path.join(root, "docs", "handoff-dashboard.html"), encoding="utf-8").read()
    check("pricing: 문서에 요금표", "요금 확인 2026-09-03" in plan and "| Supabase + Fly.io | $0–25 | $75–350 |" in plan
          and "요금 확인 2026-09-03" in dash and "supabase.com" in dash and '"pricing"' not in dash, plan[-600:])


def test_generation():
    root = to_locked(make_repo())
    cfg = util.load_config(root)
    m = design.scan(root)
    files = gen.expected(root, cfg, 1, m, api.validate(OPENAPI))
    names = sorted(os.path.basename(f) for f in files)
    check("gen: 6종 × 2 (Strings 포함)", names == sorted(["ApiRoutes.kt", "ApiRoutes.swift", "DesignTokens.kt", "DesignTokens.swift",
                                                      "Screens.kt", "Screens.swift", "Strings.kt", "Strings.swift"]), names)
    check("gen: Strings 는 화면별 그룹 (파일 = 화면)", "public enum Strings {" in files["shared/generated/Strings.swift"]
          and "public enum OrderList {" in files["shared/generated/Strings.swift"]
          and 'orderList = "Order List"' in files["shared/generated/Strings.swift"], files["shared/generated/Strings.swift"][:600])
    sw = files["shared/generated/DesignTokens.swift"]
    check("gen: 토큰 중첩·타입", "public enum Color {" in sw and 'public static let accent: String = "#0A84FF"' in sw
          and "public enum Spacing {" in sw and "public static let md: CGFloat = CGFloat(16)" in sw, sw)
    kt = files["shared/generated/ApiRoutes.kt"]
    check("gen: kotlin 라우트", "package shared.generated" in kt and 'val getOrdersByOrderId = Route("GET", "/orders/{orderId}")' in kt, kt)
    check("gen: 결정적", gen.expected(root, cfg, 1, m, api.validate(OPENAPI)) == files)
    consts = gen.token_consts(m["tokens"])
    check("gen: 토큰 상수 이름", consts["spacing.md"] == "DesignTokens.Spacing.md" and consts["size.controlLg"] == "DesignTokens.Size.controlLg", consts)
    d = tempfile.mkdtemp()
    gen.write(d, files)
    check("gen: drift 없음", gen.drift(d, files) == [])
    w(os.path.join(d, "shared/generated/Screens.swift"), "x")
    w(os.path.join(d, "shared/generated/Extra.swift"), "x")
    check("gen: drift 감지", sorted(gen.drift(d, files)) == [("extra", "shared/generated/Extra.swift"), ("modified", "shared/generated/Screens.swift")], gen.drift(d, files))
    # ios 만이면 swift 만
    root2 = to_locked(make_repo(), spec={**SPEC, "platforms": ["ios"]})
    files2 = gen.expected(root2, cfg, 1, design.scan(root2), api.validate(OPENAPI))
    check("gen: 플랫폼 게이팅", all(f.endswith(".swift") for f in files2) and len(files2) == 4, sorted(files2))


def test_happy_cycle():
    root = to_locked(make_repo())
    r = tools.build(root)
    check("build: 3 워크트리 + 프롬프트", r["ok"] and set(r["prompts"]) == {"backend", "ios", "android"}, r["message"])
    p = r["prompts"]["ios"]
    check("build: 프롬프트 내용", "SCR-01" in p and "ApiRoutes.getOrders" in p and "design/order-list.html" in p
          and "design/README.md" in p and "Project structure: xcodegen-spm" in p and "W1 [BLOCK]" in p, p)
    check("build: backend 프롬프트에 인프라", "db=postgres (supabase)" in r["prompts"]["backend"] and "DATABASE_URL" in r["prompts"]["backend"])
    check("build: backend 프롬프트에 규모", "Expected scale: small (MAU 3000 · DAU 300)" in r["prompts"]["backend"]
          and "Expected scale" not in r["prompts"]["ios"], r["prompts"]["backend"][:1500])
    for role in ("backend", "ios", "android"):
        ext = {"backend": None, "ios": ".swift", "android": ".kt"}[role]
        g = os.path.join(wt(root, role), "shared", "generated")
        check(f"build: {role} 워크트리 뼈대", os.path.isfile(os.path.join(g, "Screens.swift")) and os.path.isfile(os.path.join(g, "Screens.kt")))
    same = {util.sha_file(os.path.join(wt(root, r_), "shared/generated/Screens.swift")) for r_ in ("backend", "ios", "android")}
    check("build: 뼈대 3벌 같은 바이트", len(same) == 1)
    r2 = tools.build(root)
    check("build: 재호출은 이어가기", r2.get("resume") and r2["pending"] == ["backend", "ios", "android"])
    implement(root)
    pre = tools.precheck(root, "ios")
    check("precheck: 통과 · 소비 100", pre["passed"] and pre["consumption"]["rate"] == 100.0, pre)
    bad = tools.report(root, "ios", {"status": "weird"})
    check("report: 모양 거부", not bad["ok"] and "[R2]" in bad["message"])
    check("report: 미착수 역할 거부", not tools.report(root, "nobody", report())["ok"])
    for role in ("backend", "ios"):
        r = tools.report(root, role, report())
        check(f"report: {role} 접수", r["ok"] and "Waiting on" in r["message"], r["message"])
    r = tools.report(root, "android", report())
    check("report: 마지막 접수가 verify 자동 실행", r["ok"] and r.get("verify") and r["verify"]["verdict"] == "pass", r.get("verify", {}).get("blockers"))
    v = r["verify"]
    check("verify: 점수·성분", v["score"] >= 85 and v["components"]["hardcodes"] == 0 and v["components"]["parity_gaps"] == 0, v["components"])
    check("verify: 테스트 계약 접촉 → 자기신고 채택", v["components"]["tests_source"]["ios"] == "self-reported", v["components"])
    check("verify: 문서·대시보드", os.path.isfile(os.path.join(root, v["doc"])) and os.path.isfile(os.path.join(root, v["dashboard"])))
    html = open(os.path.join(root, v["dashboard"])).read()
    check("dashboard: 지문·화면·라우트·점수", flow.current(root, util.load_config(root))["fingerprint"] in html
          and "Screens.orderList" in html and "ApiRoutes.getOrders" in html and "badge pass" in html and "data:image/png" in html)
    st = flow.current(root, util.load_config(root))
    check("verify: pass → ship", st["phase"] == "ship")
    r = tools.ship(root, approver=None)
    check("ship: pending 프롬프트", not r["ok"] and r["pending_human"] and "점수" in r["approval_prompt"])
    r = tools.ship(root, approver=APPROVE)
    check("ship: 머지 3", r["ok"] and r["approved"] and len(r["merges"]) == 3, r["message"])
    check("ship: 워크트리 정리", not os.path.isdir(wt(root, "ios")) and not git.branch_exists(root, "handoff/ios"))
    check("ship: 본선에 코드", os.path.isfile(os.path.join(root, "apps/ios/Main.swift")) and os.path.isfile(os.path.join(root, "backend/app.py")))
    check("ship: done", flow.current(root, util.load_config(root))["phase"] == "done")
    # 새 사이클
    r = tools.import_design(root, make_package(os.path.join(os.path.dirname(root), "pkg2"), screens=("Order List", "Order Detail", "Settings", "Profile")))
    st = flow.current(root, util.load_config(root))
    check("cycle: 재등록 → api · 사이클 2", r["ok"] and st["phase"] == "api" and st["cycle"] == 2 and len(r["screens"]) == 4, (st["phase"], st["cycle"]))


def test_loop_and_handoff():
    root = to_locked(make_repo())
    cfg = util.load_config(root)
    cfg["score"]["threshold"] = 97                  # 감점이 임계치를 넘게 — 루프를 확실히 돌린다
    util.write_config(root, cfg)
    tools.build(root)
    implement(root, hardcode=True, skip_screen=("android", "settings"))
    tools.report(root, "backend", report())
    tools.report(root, "ios", report())
    r = tools.report(root, "android", report(not_done=["SCR-03 settings — ran out of time"]))
    v = r["verify"]
    check("loop: 파리티 갭 (android 만 settings 안 함)", any(g["missing"] == "android" and "settings" in g["id"] for g in v["parity"]), v["parity"])
    check("loop: 하드코딩 2 (hex + padding)", v["components"]["hardcodes"] == 2, v["components"])
    st = flow.current(root, util.load_config(root))
    ho = util.read_json(util.ho(root, util.HANDOFF))
    check("loop: 인계 파일", ho and ho["roles"]["android"]["unused"] and ho["roles"]["ios"]["hardcodes"]
          and any("settings" in x for x in ho["roles"]["android"]["parity"]), ho and ho["roles"])
    check("loop: verdict loop → build", v["verdict"] == "loop" and st["phase"] == "build" and st["roles"] == [], (v["verdict"], v["score"]))
    r = tools.build(root)
    check("loop: 재착수 프롬프트에 인계", "Carried from v1" in r["prompts"]["android"] and "Parity gaps" in r["prompts"]["android"], r["prompts"]["android"][-1500:])
    check("loop: 워크트리는 남아 이어간다", os.path.isfile(os.path.join(wt(root, "ios"), "apps/ios/Main.swift")))
    # 승인된 발산은 갭에서 빠진다
    root2 = to_locked(make_repo(), spec={**SPEC, "divergences": ["settings"]})
    tools.build(root2)
    implement(root2, skip_screen=("android", "settings"))
    for role in ("backend", "ios"):
        tools.report(root2, role, report())
    v = tools.report(root2, "android", report())["verify"]
    check("loop: 승인된 발산은 파리티 갭 아님", v["components"]["parity_gaps"] == 0, v["parity"])


def test_violations_and_secrets():
    root = to_locked(make_repo())
    tools.build(root)
    implement(root)
    t = wt(root, "ios")
    w(os.path.join(t, "api/openapi.yaml"), OPENAPI + "  /hack:\n    get: {}\n")
    w(os.path.join(t, "shared/generated/Screens.swift"), "// tampered\n")
    w(os.path.join(t, "backend/oops.py"), "x = 1\n")
    w(os.path.join(t, "apps/ios/Config.swift"), 'let apiKey = "sk_live_ABCDEFGHIJKLMNOPQRSTUV"\n')
    w(os.path.join(t, "apps/ios/.env"), "SECRET=1\n")
    git.commit_all(t, "bad")
    pre = tools.precheck(root, "ios")
    tags = [b[:4] for b in pre["blockers"]]
    check("violations: C1 C2 W1 S1 S2", not pre["passed"] and all(x in tags for x in ("[C1]", "[C2]", "[W1]", "[S1]", "[S2]")), pre["blockers"])
    check("violations: 시크릿 값이 메시지에 없다", "sk_live_ABCDEFGHIJKLMNOPQRSTUV" not in json.dumps(pre))
    r = tools.report(root, "ios", report())
    check("violations: 블로커 있는 리포트 거부", not r["ok"] and "[R1]" in r["message"])
    r = tools.report(root, "ios", report("blocked", blocked=[{"what": "x", "tried": ["a"], "error": "boom"}]))
    check("violations: 막힘 리포트는 접수", r["ok"], r["message"])
    # 이력에 넣었다 지운 키
    root2 = to_locked(make_repo())
    tools.build(root2)
    t2 = wt(root2, "backend")
    w(os.path.join(t2, "backend/cfg.py"), 'password = "hunter2hunter2hunter2"\n')
    git.commit_all(t2, "add")
    w(os.path.join(t2, "backend/cfg.py"), 'password = os.environ["PW"]\n')
    git.commit_all(t2, "remove")
    pre = tools.precheck(root2, "backend")
    check("secrets: 이력에 남은 키 잡힘", any("commit history" in b for b in pre["blockers"]), pre["blockers"])
    # 본선 오염
    w(os.path.join(root2, "stray.txt"), "x")
    pre = tools.precheck(root2, "ios")
    check("violations: W2 본선 오염", any(b.startswith("[W2]") for b in pre["blockers"]), pre["blockers"])
    os.remove(os.path.join(root2, "stray.txt"))
    # leaks 단위
    check("leaks: 예시 파일은 민감 아님", not leaks.is_sensitive_file(".env.example") and leaks.is_sensitive_file("a/.env") and leaks.is_sensitive_file("x/.ssh/id_rsa"))
    check("leaks: 자리표시는 안 잡음", not leaks.find('api_key = "YOUR_API_KEY_HERE_XX"') and leaks.find("AKIAABCDEFGHIJKLMNOP"))
    check("leaks: 마스킹", "AKIA" not in leaks.mask("key AKIAABCDEFGHIJKLMNOP end"))


def test_tests_evidence():
    root = to_locked(make_repo())
    tools.build(root)
    implement(root, tests=False)
    t = wt(root, "ios")
    w(os.path.join(t, "apps/ios/FooTests.swift"), "func testX() { XCTAssertTrue(true) }\n")
    git.commit_all(t, "tests later")
    tools.report(root, "backend", report())
    tools.report(root, "android", report())
    v = tools.report(root, "ios", report())["verify"]
    check("tests: 계약 안 건드린 스위트 → uncontracted", v["components"]["tests_source"]["ios"] == "uncontracted", v["components"])
    e = v and util.read_json(util.ho(root, tools.LAST))["roles"]["ios"]
    check("tests: 작성 순서 test-after", e["test_provenance"]["verdict"] == "test-after", e["test_provenance"])
    # skip 감지
    root2 = to_locked(make_repo())
    tools.build(root2)
    implement(root2)
    t2 = wt(root2, "backend")
    w(os.path.join(t2, "backend/tests/test_api.py"), '@pytest.mark.skip\ndef test_list():\n    assert client.get("/orders")\n')
    git.commit_all(t2, "skip")
    pre = tools.precheck(root2, "backend")
    check("tests: skip 은 표시 (블로커 아님)", pre["passed"] and len(pre["test_bypass"]["skips"]) == 1, pre["test_bypass"])
    cfg = util.load_config(root2)
    cfg["verify"]["commands"]["backend"] = ["true", "false"]
    util.write_config(root2, cfg)
    pre = tools.precheck(root2, "backend")
    check("tests: verify 명령 실패 → T1", any("[T1]" in b for b in pre["blockers"]), pre["blockers"])


def test_report_and_state():
    p = score.validate_report({"status": "blocked", "blocked": [{"what": "x"}]})
    check("report: blocked 는 tried+error 필요", any("tried" in x for x in p))
    check("report: 정상", score.validate_report(report()) == [])
    root = to_locked(make_repo())
    st = util.read_state(root)
    check("state: 기록", [h["ev"] for h in st["history"]][:4] == ["design_imported", "spec_saved", "api_submitted", "locked"], [h["ev"] for h in st["history"]])
    r = tools.status(root)
    check("status: 요약", r["ok"] and r["phase"] == "build" and r["design"]["screens"] == ["orderDetail", "orderList", "settings"] and r["fingerprint"] == st["locked"]["hash"], r)
    check("status: 미배선", not tools.status(tempfile.mkdtemp())["ok"])


def test_candidates():
    base = tempfile.mkdtemp()
    make_package(os.path.join(base, "my-export"))                      # README 에 스택 힌트만 — project/ 도 없다
    with open(os.path.join(base, "my-export", "README.md"), "a") as f:
        f.write("\nThis is a **handoff bundle** from Claude Design.\n")
    os.makedirs(os.path.join(base, "unpacked", "project", "_ds"))      # zip 을 푼 모양
    os.makedirs(os.path.join(base, "design"))                          # 도구 폴더 — 후보 아님
    os.makedirs(os.path.join(base, "src"))                             # 평범한 폴더 — 후보 아님
    with open(os.path.join(base, "src", "index.html"), "w") as f:
        f.write("<html><body>hi</body></html>")
    zp = os.path.join(base, "pkg.zip")
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("wrapper/README.md", "# x")
    with open(os.path.join(base, "notes.txt"), "w") as f:
        f.write("x")
    r = tools.status(base)
    paths = [c["path"] for c in r["candidates"]]
    check("candidates: 미배선에서도 찾는다", paths == ["my-export/", "unpacked/", "pkg.zip"], paths)
    check("candidates: 메시지에 든다", "패키지 후보" in r["message"] and "고르게" in r["message"], r["message"])
    tools.setup(base)
    r = tools.status(base)
    check("candidates: import 단계", r["phase"] == "import" and [c["path"] for c in r["candidates"]] == paths, r)
    for n in ("unpacked", "pkg.zip"):
        shutil.rmtree(os.path.join(base, n), ignore_errors=True) if os.path.isdir(os.path.join(base, n)) else os.remove(os.path.join(base, n))
    r = tools.status(base)
    check("candidates: 하나면 바로 제안", 'import_design(path="my-export")' in r["message"], r["message"])
    tools.import_design(base, "my-export")
    check("candidates: 등록 뒤엔 안 보인다", "candidates" not in tools.status(base), tools.status(base).get("candidates"))


def test_screens_page():
    root = to_locked(make_repo())
    tools.build(root)
    implement(root)
    for role, name in (("ios", "orderList.png"), ("android", "orderList__dark.png")):
        p = os.path.join(wt(root, role), ".handoff", "shots", name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(PNG)
    for role in ("backend", "ios"):
        tools.report(root, role, report())
    v = tools.report(root, "android", report())["verify"]
    check("shots: 페이지", v["screens_page"] and os.path.isfile(os.path.join(root, v["screens_page"])), v.get("screens_page"))
    html = open(os.path.join(root, v["screens_page"])).read()
    check("shots: 디자인 iframe + 두 앱 사진", "design/order-list.html" in html and "ios/orderList.png" in html and "android/orderList__dark.png" in html)
    check("shots: 자기 무시 폴더", os.path.isfile(os.path.join(root, "docs/handoff-screens/.gitignore")))
    check("shots: 워크트리 .handoff 는 담당 밖 아님", tools.precheck(root, "ios")["passed"])


def test_hooks():
    hook = os.path.join(HERE, "..", "hooks", "guard.py")
    root = to_locked(make_repo())

    def run(tool, inp):
        env = {**os.environ, "CLAUDE_PROJECT_DIR": root}
        payload = json.dumps({"tool_name": tool, "tool_input": inp, "cwd": root})
        r = subprocess.run([sys.executable, hook], input=payload, capture_output=True, text=True, env=env, cwd=root)
        try:
            return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"]
        except Exception:
            return "allow"

    cases = [
        ("Write", {"file_path": os.path.join(root, "design/x.html")}, "deny"),
        ("Edit", {"file_path": os.path.join(root, "api/openapi.yaml")}, "deny"),
        ("Write", {"file_path": os.path.join(root, ".handoff/state.json")}, "deny"),
        ("Write", {"file_path": os.path.join(root, ".handoff/worktrees/ios/apps/ios/A.swift")}, "allow"),
        ("Write", {"file_path": os.path.join(root, ".handoff/worktrees/ios/shared/generated/X.swift")}, "deny"),
        ("Write", {"file_path": os.path.join(root, ".handoff/worktrees/ios/api/openapi.yaml")}, "deny"),
        ("Read", {"file_path": os.path.join(root, "design/x.html")}, "allow"),
        ("Read", {"file_path": os.path.join(root, "backend/.env")}, "deny"),
        ("Write", {"file_path": os.path.join(root, "backend/.env.example")}, "allow"),
        ("Bash", {"command": "cat api/openapi.yaml"}, "allow"),
        ("Bash", {"command": "echo x > api/openapi.yaml"}, "deny"),
        ("Bash", {"command": "rm -rf design"}, "deny"),
        ("Bash", {"command": "cat .env"}, "deny"),
        ("Bash", {"command": "python3 server/run.py ship --root ."}, "deny"),
        ("Bash", {"command": "ls apps/ios"}, "allow"),
    ]
    for tool, inp, want in cases:
        got = run(tool, inp)
        check(f"hook: {tool} {json.dumps(inp)[:60]} → {want}", got == want, got)
    other = tempfile.mkdtemp()
    r = subprocess.run([sys.executable, hook], input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": os.path.join(other, "design/x")}, "cwd": other}),
                       capture_output=True, text=True, cwd=other)
    check("hook: 미배선 레포는 통과", r.stdout.strip() == "")


TESTS = [test_candidates, test_yaml_and_routes, test_design_scan, test_bundle_and_states, test_derive, test_flow_gates, test_infra, test_generation, test_happy_cycle,
         test_loop_and_handoff, test_violations_and_secrets, test_tests_evidence, test_report_and_state,
         test_screens_page, test_hooks]


def main():
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    for t in TESTS:
        if only and not any(o in t.__name__ for o in only):
            continue
        print(t.__name__)
        try:
            t()
        except Exception:
            traceback.print_exc()
            FAILS.append(t.__name__ + " (exception)")
    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}: " + ", ".join(FAILS))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
