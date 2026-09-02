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

## 설치

```
/plugin marketplace add https://github.com/yeonghunyoo/handoff-agent
/plugin install handoff
```

→ 세션 재시작. 개발할 레포에서 `/handoff` 를 치면 배선(`.handoff/config.json` · `.gitignore` · `CLAUDE.md` 절)까지
그 자리에서 된다. macOS/Linux · Python 3.10+ · git(`user.name`/`user.email` 설정) 필요. 첫 기동 때 `mcp` 를
`server/.venv` 에 받는다(네트워크 1회).

## 프로세스

✋ = 사람이 판단하는 자리.

| 단계 | 무슨 일이 일어나나 | 사람이 할 일 |
|---|---|---|
| ① 패키지 등록 | 링크(`claude.ai/design/p/…`) · 내보낸 zip/tar.gz · standalone HTML(번들 자동 펼침) · 폴더를 `design/` 로 가져와 화면·토큰·문서를 발견한다 | 링크나 파일 경로를 준다. **화면 목록을 확정한다** (프로토타입 한 파일 안의 상태 분기가 화면 후보로 뽑히고, 시트·오버레이는 직접 추가) |
| ② 스펙 | 플랫폼 · 백엔드 스택 · 인프라(db/auth/hosting/env) **결정만 기록** | 후보 비교를 보고 직접 고른다 |
| ③ 백엔드 계약 | 화면·문서에서 필요한 데이터로 `api/openapi.yaml` 초안 | — |
| ④ 계약 확정 ✋ | 대시보드(화면 갤러리·토큰·라우트·결정)를 보인다 | 지문을 대조하고 승인/반려 |
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

1. 사람이 확정한 `design/handoff.manifest.json` 이 있으면 그것 (`import_design(screens=…)` 가 쓴다). 패키지 자체의 최상위 json 에 `screens`/`tokens` 가 있어도 같은 취급
2. 화면 = `*.dc.html`/`*.html` 파일 하나 (`Order List.dc.html` → `Screens.orderList`). 아트보드 하나에 `<sc-if value="{{ isHome }}">` 상태 분기가 여럿이면 **그 분기들이 화면 후보** (프로토타입). 단일 html 의 `<section id>` 도 화면. `design_doc_mode=canvas` 보드(여러 안 비교)는 화면이 아니라 참고 자료
3. 토큰 = css/html 의 `--커스텀-프로퍼티` + `*token*.json` (W3C `$value` 포함) → 색·치수·기타
4. 문서 = `*.md` (README 먼저 — 스택 힌트를 인터뷰 기본값으로 제안한다) · `chats/` 는 대화 기록(의도) · 스크린샷 = 화면 이름과 맞는 이미지
5. standalone HTML 내보내기(`__bundler/manifest`)는 아트보드 + jsx + 런타임 + 폰트로 펼친다 (CDN 의존성은 뺀다)

화면이 0개면 등록을 거부한다. 토큰이 0개면 경고만(하드코딩 검출 비활성).

### 파생물 — `design/derived/` (서버가 결정적으로 뽑는다)

네이티브 구현에 화면·토큰만으로는 부족한 다섯 가지를 프로토타입에서 규칙으로 뽑아 계약(지문)에 넣는다:

| 파일 | 출처 → 규칙 | 쓰임 |
|---|---|---|
| `intent.md` | 대화 기록의 **사용자 턴**만 시간순 (JSON 이 256 KiB 에서 잘리므로 정규식) | 착수 프롬프트 맨 위 — 화면에 안 보이는 동작 규칙 |
| `entities.json` | 스크립트의 `const NAME = [...]` + 초기 `state` (JS 리터럴 파서) | 도메인 모델 · 시드 · openapi 초안 입력 |
| `strings.json` → `Strings.*` | 텍스트 노드(상태 분기·오버레이로 화면 귀속) + 데이터 문구 + 템플릿 조각. 키는 한글 로마자 (`onboarding.geonneottwigi`) | 앱 코드의 한글 리터럴 = `raw-string` 하드코딩 |
| `icons.json` + `icons/*.svg` → `Icons.*` | 인라인 SVG 를 path 해시로 중복 제거, 이름은 근처 핸들러 (`setTabHome` → `tabHome`) | `ICN-xx` 소비 항목 · 파리티 |
| `behavior.json` | 핸들러 → 바꾸는 state 키 · 탭 전이 · 타이머(ms) | 두 플랫폼이 같은 전이표를 구현 |
| `rules.json` | 디자인 시스템 `_adherence`(hex·px·폰트 금지) | `raw-font` 검출 등 검사 규칙이 디자인 시스템을 따라간다 |

## Claude Design 과의 연결 — `/claude-design`

Claude Design 은 웹 제품이고, Claude Code 에서 닿는 통로는 넷이다. `/claude-design` 스킬(과 같은 이름의 에이전트)이
기능 하나하나를 통로에 대응시킨 **인터페이스 레이어**다:

| 통로 | 되는 것 |
|---|---|
| Claude Design MCP 커넥터 (`claude mcp add --scope user --transport http claude-design https://api.anthropic.com/v1/design/mcp`) | 프로젝트·파일·핸드오프를 프로그램으로. "Send to local coding agent" 가 기대하는 것 |
| DesignSync (`/design-sync`) | 디자인 시스템 올리기/내리기 · projectId 로 파일 읽기 (커넥터 폴백) |
| 내보낸 파일 | zip · tar.gz · standalone HTML(`run.py unbundle` 또는 `import_design` 이 펼친다) |
| `/design` 로컬 캔버스 | 웹 없이 `.dc.html` 초안 — 같은 형식이라 `import_design` 에 넣을 수 있다 |

코멘트 · 캔버스 편집 · 공유 · Export 클릭은 웹 전용이다 — 스킬이 정확히 무엇을 누를지 안내하고 멈춘다.

## 보는 곳

| 산출물 | 위치 |
|---|---|
| 대시보드 (정본 — 아티팩트로 발행) | `docs/handoff-dashboard.html` |
| 계약 요약 · 검사 결과 (텍스트) | `docs/handoff-plan.md` · `docs/handoff-verify.md` |
| 화면 대조 (디자인 원본 \| iOS \| Android) | `docs/handoff-screens/index.html` — 에이전트가 `<워크트리>/.handoff/shots/<화면id>.png` 를 남기면 |
| 실시간 현황판 | `python3 <플러그인 루트>/server/run.py dashboard --root .` |

승인 채널은 elicitation 과 터미널(`run.py review|ship --root .`) 둘뿐이다 — 에이전트가 승인을 위조할 수 없다.

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
