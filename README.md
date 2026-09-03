# handoff

**Claude Design 핸드오프 패키지 하나로 iOS · Android · backend 를 한 번에 만드는 에이전트.**
기억할 명령은 `/handoff` 하나다.

## 무엇을 해주나

디자인은 [claude.ai/design](https://claude.ai/design) 에서 끝낸다 — 거기서 루프를 돌고, 다 됐으면 **핸드오프
패키지**(화면 HTML · 개발 문서 · 토큰 · 스크린샷)를 내보낸다. 이 도구는 그 패키지를 **프론트 계약**으로 받고,
사람이 스택·인프라를 결정하면 화면에서 **백엔드 계약(openapi.yaml)** 을 초안한다. 사람이 계약을 확정하면
세 에이전트가 각자 격리된 git 워크트리에서 병렬로 구현하고, 서버가 코드·diff 를 **직접 재서** 계약대로 된 것만
사람 승인 뒤 main 에 머지한다.

- 사람이 판단하는 자리는 둘 — **계약 확정** · **완료 승인**. 나머지는 자동이다
- 판정은 에이전트의 자기 신고가 아니라 **서버가 실측한 것**이다 — 소비율 · 하드코딩 · 파리티 · 테스트 · 시크릿
- 디자인을 여기서 고치지 않는다. 고칠 게 있으면 Claude Design 에서 고쳐 다시 내보낸다

## 시작하기 — 사람이 직접 해야 하는 것

에이전트가 대신 못 하는 일이다(로그인 · 사용자 범위 설정 · 세션 재시작). 순서대로 한 번만 하면 된다.
`/handoff` 를 처음 부르면 아래 중 빠진 것을 찾아 알려 준다.

- [ ] **1. 준비물** — macOS/Linux · Python 3.10+ · git(`user.name`/`user.email` 설정) · Claude Code 로그인
- [ ] **2. 리포 클론 (선택)** — 마켓플레이스로 바로 깔아도 되고, 고쳐 쓰려면 클론한다
  ```
  git clone https://github.com/yeonghunyoo/handoff-agent
  ```
- [ ] **3. 이 README 를 끝까지 읽는다** — 특히 아래 "프로세스" 표의 ✋ 두 자리(계약 확인 · 완료 승인)가 당신이 판단할 자리다
- [ ] **4. handoff 플러그인 등록** — Claude Code 안에서
  ```
  /plugin marketplace add https://github.com/yeonghunyoo/handoff-agent   # 클론했으면 그 경로
  /plugin install handoff
  ```
  → **세션 재시작**. 첫 기동 때 `mcp` 를 `server/.venv` 에 받는다(네트워크 1회).
  확인: `claude plugin list` 에 `handoff` 가 enabled 로 보이면 된다
- [ ] **5. Claude Design MCP 커넥터 등록** — 링크(`claude.ai/design/p/…`)로 디자인을 바로 받으려면 필요하다. 사용자 범위 설정이라 터미널에서 직접 친다
  ```
  claude mcp list                                   # claude-design 이 이미 있으면 6 으로
  claude mcp add --scope user --transport http claude-design https://api.anthropic.com/v1/design/mcp
  ```
  → **세션 재시작**
- [ ] **6. 로그인** — 등록만으로는 안 붙는다. Claude Code 안에서
  ```
  /design-login       # Claude Design 로그인 (DesignSync · 커넥터 폴백 경로)
  /mcp                # claude-design 이 connected 인지 확인 · 안 돼 있으면 여기서 인증
  ```
  건너뛰면 링크 대신 zip 내보내기(`Export → Handoff → Download zip`)로만 받을 수 있다
- [ ] **7. 개발할 레포에서 `/handoff`** — 배선(`.handoff/config.json` · `.gitignore` · `CLAUDE.md` 절)은 그 자리에서 된다

이후 사람이 손대는 지점은 프로세스 표의 ✋ 둘과, 승인창(elicitation)이 안 뜰 때의 터미널 승인
(`python3 <플러그인 루트>/server/run.py review|ship --root .`) 뿐이다.

## 프로세스

✋ = 사람이 판단하는 자리.

| 단계 | 무슨 일이 일어나나 | 사람이 할 일 |
|---|---|---|
| ① 패키지 등록 | 링크(`claude.ai/design/p/…`) · 내보낸 zip/tar.gz · standalone HTML(번들 자동 펼침) · 폴더를 `design/` 로 가져와 화면·컴포넌트(타입)·토큰·문서를 발견한다 | 레포 루트에 zip·번들 html·푼 폴더를 두면 `/handoff` 가 찾아서 제안한다 — 경로를 말할 필요가 없다. 다른 곳에 있으면 링크나 경로를 준다. **화면 목록을 확정한다** (프로토타입 한 파일 안의 상태 분기가 화면 후보). 시트·모달·팝오버는 화면이 아니라 **컴포넌트**로 잡히니 이름·타입만 보고 필요하면 고친다 |
| ② 스펙 | 플랫폼 · 백엔드 스택 · 규모(예상 MAU·DAU → 소규모 / 중규모 이상) · 인프라(db/auth/hosting/env) **결정만 기록** | 규모를 먼저 정하면 그 규모에 맞는 인프라 조합 **4~5개**(그날 후보의 요금 페이지에서 새로 읽은 월 비용 구간 · 확인일 · 출처 · 장단점 · 종속성)를 보고 직접 고른다. 나머지 조합은 id 로 부를 수 있다 |
| ③ 백엔드 계약 | 화면·문서에서 필요한 데이터로 `api/openapi.yaml` 초안 | — |
| ④ 계약 확정 ✋ | 요약 표(디자인 출처 · 화면 · 계약 지문 · 선택한 인프라)를 채팅에 보인다 | 지문을 대조하고 승인/반려 |
| ⑤ 구현 | 역할별 워크트리 + 생성 상수(`ApiRoutes` `Screens` `DesignTokens`) + 착수 프롬프트로 병렬 구현 | 기다린다 |
| ⑥ 검사 | 서버가 재검사해 점수 → `loop`(인계 후 재착수) 또는 `pass` | 계약 수정 제안이 있으면 판단 |
| ⑦ 완료 승인 ✋ | 예외 항목(테스트 skip·하드코딩·플랫폼 차이)과 함께 보인다 | 승인 → main 머지 |

앞 단계로 돌아가고 싶으면 아무 때나 말하면 된다(`back`). 잠금 뒤 `design/`·`api/` 가 바뀌면 자동으로 재승인을 요구한다.

## 점수

`점수 = 0.4·소비 + 0.3·테스트 + 0.3·파리티`, 85 이상이고 블로커가 없으면 `pass` (`.handoff/config.json` 으로 조정).

- **소비** — 역할별 계약 항목(라우트 `API-xx` · 화면 `SCR-xx`) 소비율 − 하드코딩 감점(hex 색 · 토큰에 이름 있는 치수 · 라우트 원문)
- **테스트** — 설정된 `verify.commands` 를 서버가 재실행한 결과 > 자기 신고. 계약 상수를 하나도 안 건드린 스위트는 증거가 아니라 제외
- **파리티** — 미승인 발산 + 실측 갭(iOS↔Android 소비 항목·토큰 집합 차)
- **블로커** — `design/`·`api/` 변경, 생성 상수 드리프트, 담당 밖 쓰기, 본선 오염, 시크릿(코드·민감 파일·커밋 이력), 빌드 실패

## 핸드오프 패키지 — 무엇을 읽나

포맷은 고정돼 있지 않다. 발견 규칙으로 읽는다(관대하다):

1. 사람이 확정한 `design/handoff.manifest.json` 이 있으면 그것 (`import_design(screens=…, components=…)` 가 쓴다). 패키지 자체의 최상위 json 에 `screens`/`components`/`tokens` 가 있어도 같은 취급
2. 화면 = `*.dc.html`/`*.html` 파일 하나 (`Order List.dc.html` → `Screens.orderList`). 아트보드 하나에 `<sc-if value="{{ isHome }}">` 상태 분기가 여럿이면 **그 분기들이 화면 후보** (프로토타입). 단일 html 의 `<section id>` 도 화면. `design_doc_mode=canvas` 보드(여러 안 비교)는 화면이 아니라 참고 자료
3. 토큰 = css/html 의 `--커스텀-프로퍼티` + `*token*.json` (W3C `$value` 포함) → 색·치수·기타
4. 문서 = `*.md` (README 먼저 — 스택 힌트를 인터뷰 기본값으로 제안한다) · `chats/` 는 대화 기록(의도) · 스크린샷 = 화면 이름과 맞는 이미지
5. standalone HTML 내보내기(`__bundler/manifest`)는 아트보드 + jsx + 런타임 + 폰트로 펼친다 (CDN 의존성은 뺀다)

화면이 0개면 등록을 거부한다. 토큰이 0개면 경고만(하드코딩 검출 비활성).

### 매니페스트 — `design/handoff.manifest.json` (v2)

사람이 화면(또는 컴포넌트)을 확정하면 생기고, 그 뒤로는 `import_design` 마다 서버가 상세를 다시 채운다 — 사람이 정한 것
(화면 목록 · `components_confirmed` 일 때 컴포넌트의 id·type·title)만 남기고 나머지는 `design/` 에서 결정적으로 재계산한다
(시각 같은 비결정 값은 안 넣는다 — 지문에 들기 때문). 구현 에이전트가 맨 먼저 읽는 색인이다.

| 키 | 내용 |
|---|---|
| `screens[]` | `id · title · file · anchor` + 그 화면의 `components[]`(id) · `strings[]`(키) · `icons[]`(이름) · `shots[]` |
| `components[]` | 상호작용 요소를 **타입**으로 분류 — `sheet` `modal` `popover`(오버레이: `anchor`=state 키 · `style` 변수 · `open[]`/`close[]` 핸들러 · `children[]` · `strings[]` · `icons[]`) · `tab`(`target`) · `button`(`handler` · `sets[]` · `icon`) · `toggle` · `input`/`slider`(`bind` · `placeholder` · `min`/`max`) · `item`(반복 항목: `list`) · `gesture`(`events[]`). 공통: `screen`(귀속) · `screens[]` · `uses` · `file` |
| `components_confirmed` · `component_types` | 사람이 컴포넌트를 확정했는지 · 타입별 개수 |
| `navigation` | `entry`(진입 화면) · `tabs{핸들러: 화면}` · `transitions[{via, to, action}]` — action 은 `go`/`leave`(화면) · `open`/`close`/`toggle`(오버레이) |
| `state` | 프로토타입의 초기 state 통째 |
| `entities{}` | 데이터 배열 이름 → `count` · `fields[]` (본문은 `derived/entities.json`) |
| `strings` · `icons` · `tokens` | 개수 · 파일 · 아이콘 이름 · 토큰 종류별 개수 |
| `docs` · `chats` · `boards` · `assets` | 문서 · 대화 기록 · 탐색 보드 · 나머지 자산 경로 |

**오버레이는 화면이 아니라 컴포넌트다.** `style="{{ settingsSheetStyle }}"` 같은 스타일 바인딩과 state 의 `*Open` 키로 잡는다
(이름에 sheet/popover/modal 이 없으면 state 키와 줄기가 정확히 같을 때만 modal). 사람이 `screens=…` 로 같은 anchor 를 화면으로
올리면 화면이 이기고, `components=[{id,type,title,anchor}]` 로 이름·타입을 고칠 수 있다. 확정하지 않아도 서버 추출분이 그대로 실린다.

### 파생물 — `design/derived/` (서버가 결정적으로 뽑는다)

네이티브 구현에 화면·토큰만으로는 부족한 것들을 프로토타입에서 규칙으로 뽑아 계약(지문)에 넣는다:

| 파일 | 출처 → 규칙 | 쓰임 |
|---|---|---|
| `intent.md` | 대화 기록의 **사용자 턴**만 시간순 (JSON 이 256 KiB 에서 잘리므로 정규식) | 착수 프롬프트 맨 위 — 화면에 안 보이는 동작 규칙 |
| `entities.json` | 스크립트의 `const NAME = [...]` + 초기 `state` (JS 리터럴 파서) | 도메인 모델 · 시드 · openapi 초안 입력 |
| `strings.json` → `Strings.*` | 텍스트 노드(상태 분기·오버레이로 화면 귀속) + 데이터 문구 + 템플릿 조각. 키는 한글 로마자 (`onboarding.geonneottwigi`) | 앱 코드의 한글 리터럴 = `raw-string` 하드코딩 |
| `icons.json` + `icons/*.svg` → `Icons.*` | 인라인 SVG 를 path 해시로 중복 제거, 이름은 근처 핸들러 (`setTabHome` → `tabHome`) | `ICN-xx` 소비 항목 · 파리티 |
| `behavior.json` | 핸들러 → 바꾸는 state 키 · 탭 전이 · 타이머(ms) | 두 플랫폼이 같은 전이표를 구현 |
| `components.json` | 클릭·입력·제스처 속성과 오버레이 스타일 블록 → 타입(`sheet` `modal` `popover` `tab` `button` `toggle` `input` `slider` `item` `gesture`) · 귀속 화면(상태 분기 + `renderVals` 의 탭 비교 플래그 + 오버레이 구역) | 매니페스트 `components` · 착수 프롬프트의 Components 절 |
| `navigation.json` | 초기 state 의 `is*`=true 진입 화면 · `setTab` 전이 · 핸들러 본문의 state 리터럴(`tab: 'home'` · `settingsOpen: true`) | 매니페스트 `navigation` — 두 플랫폼이 같은 전이 그래프 |
| `rules.json` | 디자인 시스템 `_adherence`(hex·px·폰트 금지) | `raw-font` 검출 등 검사 규칙이 디자인 시스템을 따라간다 |
| `layout/<screen>.json` | 화면 구역의 마크업을 **무손실** 노드 트리로 — 스타일 속성 전부 + 토큰(`var(--x)`·값이 맞는 hex·종류가 맞는 px)은 `DesignTokens.*`, 문구는 `Strings.*`(포맷은 `params`), svg 는 `Icons.*`, `sc-if`→`when`, `sc-for`→`items/as`, 핸들러→`on_*` | 빌더가 HTML 을 다시 읽어 화면을 재구성하지 않고 트리를 그대로 옮긴다. 실측(2026-09-04, 화면 2×플랫폼 2×2회): 분기·바인딩 누락이 줄고 빌더 토큰 −39% · 시간 −58% |

## Claude Design 과의 연결 — `/claude-design`

Claude Design 은 웹 제품이고, Claude Code 에서 닿는 통로는 넷이다. `/claude-design` 스킬(과 같은 이름의 에이전트)이
기능 하나하나를 통로에 대응시킨 **인터페이스 레이어**다:

| 통로 | 되는 것 |
|---|---|
| **내보낸 파일 — 기본** | zip · tar.gz · standalone HTML(`run.py unbundle` 또는 `import_design` 이 펼친다). 서버가 로컬에서 풀어 **모델 컨텍스트를 안 쓴다**. README·`chats/` 는 zip 에만 들어 있다 |
| Claude Design MCP 커넥터 (`claude mcp add --scope user --transport http claude-design https://api.anthropic.com/v1/design/mcp`) | 링크로 프로젝트·파일을 읽는다. "Send to local coding agent" 가 기대하는 것. 단, 파일 본문이 모델 컨텍스트를 지나고 **파일당 256 KiB 상한** — 작은 프로젝트용. 큰 파일은 나눠 읽거나 zip 으로 |
| DesignSync (`/design-sync`) | 디자인 시스템 올리기/내리기 · projectId 로 파일 읽기 (커넥터 폴백, 같은 상한·나눠 읽기 없음) |
| `/design` 로컬 캔버스 | 웹 없이 `.dc.html` 초안 — 같은 형식이라 `import_design` 에 넣을 수 있다 |

코멘트 · 캔버스 편집 · 공유 · Export 클릭은 웹 전용이다 — 스킬이 정확히 무엇을 누를지 안내하고 멈춘다.

## 보는 곳

| 산출물 | 위치 |
|---|---|
| 요약 표 · 정합성 체크리스트 (채팅) | `status` · `api_submit` · `verify` · `ship` 응답의 `summary.markdown` · `checklist.markdown` — 터미널은 `run.py status --root .` |
| 계약 요약 · 검사 결과 (텍스트) | `docs/handoff-plan.md` · `docs/handoff-verify.md` |
| 화면 대조 (디자인 원본 \| iOS \| Android) | `docs/handoff-screens/index.html` — 에이전트가 `<워크트리>/.handoff/shots/<화면id>.png` 를 남기면 |

승인 채널은 elicitation 과 터미널(`run.py review|ship --root .`) 둘뿐이다 — 에이전트가 승인을 위조할 수 없다.

## 보안 — 민감 파일 · 개인정보

| 어디서 | 무엇을 |
|---|---|
| 훅 (`hooks/guard.py`) | `.env` · 키 · 인증서 · credentials · `.ssh/` `.aws/` 등 민감 파일의 Read·Grep·Glob·Edit·Write·Bash 를 막는다 (홈 디렉터리 경로 포함). `env`·`printenv` 전체 덤프도 막는다. `.example` 은 자유 |
| 패키지 등록 (`import_design`) | 민감 파일은 `design/` 에서 뺀다. 텍스트의 시크릿, `chats/`·README 의 개인정보(이메일·휴대전화·주민번호·카드번호)는 마스킹한다. 원본 zip 은 그대로. 결과는 응답 `security` 에 |
| 서버 응답 · 상태 · 문서 | MCP 응답·CLI 출력·`.handoff/` 이력·리포트·`docs/` 전부 시크릿 + 개인정보 마스킹(`•••`) |
| 대상 레포 `.gitignore` | `setup` 이 민감 파일 목록(`leaks.SENSITIVE_GLOBS`)을 그대로 넣는다 |
| 검사 (`verify`) | 워크트리 코드·커밋 이력의 시크릿 값은 [S1] 블로커 |
| 플러그인 자체 | 테스트가 리포 전체를 훑어 개인 절대 경로·개인정보·시크릿·민감 파일이 없는지 확인한다 (`bash tests/run.sh security`) |

패턴은 경보다 — 블랙리스트는 진다. 그래도 없는 것보다 낫다.

## 레포에 깔리는 것

`.handoff/`(상태·리포트·워크트리 — 커밋 안 됨) · `design/`(패키지) · `api/openapi.yaml` · `shared/generated/`(워크트리에만) ·
`docs/handoff-*` · `.gitignore` 몇 줄 · `CLAUDE.md` 의 handoff 절. 설정은 `.handoff/config.json` (담당 경로 `roles` ·
`score.threshold` · `verify.commands` — 이 명령은 서버가 셸로 실행하므로 config 가 git 에 추적되면 실행을 거부한다).

## 검증

```bash
bash tests/run.sh          # 전체 (임시 픽스처 — 레포를 더럽히지 않는다)
bash tests/run.sh hooks    # 이름 조각으로 좁혀
```

라이선스: MIT
