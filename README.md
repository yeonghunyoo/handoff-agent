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
| ① 패키지 등록 | zip/폴더를 `design/` 로 가져와 화면·토큰·문서를 발견한다 | 내보낸 패키지 경로를 준다. 화면 목록을 확인한다 |
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

1. 최상위 json 에 `screens`/`tokens` 가 있으면 그것을 믿는다
2. 화면 = html 파일 하나 (`01-order-list.html` → `Screens.orderList`). html 이 하나뿐이고 안에 `<section id>`/`data-screen` 이 여럿이면 그 섹션들
3. 토큰 = css/html 의 `--커스텀-프로퍼티` + `*token*.json` (W3C `$value` 포함) → 색·치수·기타
4. 문서 = `*.md` (README 먼저 — 스택 힌트를 인터뷰 기본값으로 제안한다) · 스크린샷 = 화면 이름과 맞는 이미지

화면이 0개면 등록을 거부한다. 토큰이 0개면 경고만(하드코딩 검출 비활성).

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
