# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 무엇인가

Claude Code 플러그인. Claude Design 핸드오프 패키지를 프론트 계약으로, `api/openapi.yaml` 을 백엔드 계약으로 받아
iOS · Android · backend 를 git 워크트리에서 병렬 구현하고, 서버가 실측한 것만 사람 승인 뒤 머지한다.
설계 원칙과 금지 사항은 `AGENT.md` 가 정본이다 — 코드를 고치기 전에 먼저 읽는다. 이 파일은 그것을 반복하지 않는다.

이 리포는 플러그인 **자체**다. `design/` `api/` `.handoff/` 는 여기가 아니라 플러그인을 쓰는 대상 레포에 생긴다.

## 명령

```bash
bash tests/run.sh                 # 전체 — cases.py + (server/.venv 가 있으면) mcp_cases.py
bash tests/run.sh parity          # 이름 조각으로 좁혀 (여러 조각 가능: bash tests/run.sh hook derive)
python3 tests/cases.py parity     # 위와 같음, MCP 계층 제외
server/.venv/bin/python3 tests/mcp_cases.py   # MCP 계층만 (elicitation 승인 경로)
```

- 테스트는 임시 디렉터리에 가짜 레포·패키지를 만들어 전체 사이클을 돈다. 리포를 더럽히지 않고 픽스처 파일도 없다 — 픽스처는 `tests/cases.py` 안의 상수(`OPENAPI` `SPEC` 등)다.
- MCP 계층 검사는 `server/.venv` 가 필요하다. `python3 server/run.py serve` 를 한 번 띄우면 자동 생성되고(`mcp==2.1.1` 고정), CI 는 이 검사가 건너뛰어지면 실패로 잡는다. 수동 생성: `python3 -m venv server/.venv && server/.venv/bin/pip install mcp==2.1.1`.
- 의존성은 `mcp` 하나뿐이고 `server/handoff/server.py` 만 import 한다. 나머지는 stdlib. YAML 도 자체 부분집합 파서(`api.py`)다 — PyYAML 을 넣지 않는다.
- git `user.name`/`user.email` 이 설정돼 있어야 테스트가 돈다.

사람용 CLI (대상 레포에서, `--root` 는 그 레포):

```bash
python3 server/run.py status|setup|review|ship --root <레포>
python3 server/run.py unbundle <standalone.html> <폴더>
```

## 구조 — 한눈에

```
server.py (MCP, 도구 13개)  ─┐
run.py    (CLI · 런처)       ─┴→ tools.py (순수 함수) → flow · design · derive · api · infra · gen · checks · score · reports · git · util
hooks/guard.py               ─ 독립 (stdlib, 서버 import 금지)
```

- **`tools.py` 가 유일한 진입 API**다. MCP 와 CLI 가 같은 함수를 부르므로 도구를 추가·수정하면 `server.py` 의 래퍼와 `tests/cases.py` 도 같이 고친다.
- **승인은 `approver` 콜백**으로만 통과한다. `tools.review/ship(root, approver=None)` 은 approver 가 없으면 `pending_human` + `approval_prompt` 만 돌려준다. MCP 는 elicitation, CLI 는 tty 로 콜백을 채운다. 승인 도구에 approve 류 인자를 추가하지 않는다.
- **단계는 `flow.py`** 의 `PHASES` 순서이고, `flow.current()` 가 매번 저장 상태를 실측과 대조한다 (design/+api/ 트리 해시 `util.fingerprint` 가 잠금 지문과 다르면 review 로 강등). 도구는 `flow.require(st, *phases)` 로 단계를 강제한다.

### 파이프라인 안에서 데이터가 어디서 나와 어디로 가나

| 단계 | 읽는 것 | 만드는 것 |
|---|---|---|
| import | zip/tar.gz/standalone HTML/폴더 | `design/` (정본, 읽기 전용) · `design/derived/*` (`derive.write_all` — 문구·아이콘·모델·전이·**컴포넌트(타입)**·**내비게이션**·의도·규칙) · 사람이 확정하면 `design/handoff.manifest.json` v2 (`design.confirm_screens` 가 화면·컴포넌트를 쓰고 `design.write_manifest` 가 상세를 채운다) |
| spec | 사람 답 (부분 저장 누적: `.handoff/spec.draft.json`) | `.handoff/spec.json` |
| api | `api_submit` 본문 | `api/openapi.yaml` (`api.validate` 통과분만) |
| review ✋ | 위 셋 | `state.locked.hash` + 본선 커밋 |
| build | 계약 | 역할별 브랜치 `handoff/<role>` + 워크트리 `.handoff/worktrees/<role>` + `shared/generated/*` (`gen.expected`) + 영어 착수 프롬프트 (`reports.kickoff`) |
| precheck / verify | 워크트리 코드·diff | `score.evaluate_role` 결과 · loop 면 `.handoff/handoff.json` 인계 |
| ship ✋ | 재검사 결과 | `git.merge` |

사람이 보는 것은 `status`/`api_submit` 의 `summary`(디자인 출처 · 계약 · 선택한 인프라 표)와 `verify`/`status` 의 `checklist`(투두식 정합성 목록 + 분석)다 — 둘 다 `reports.py` 가 md 로 렌더링한다.

`design.scan()` 은 매니페스트를 저장하지 않고 매번 `design/` 에서 결정적으로 다시 계산한다. 화면 목록을 바꾸는 유일한 방법은 `handoff.manifest.json` (사람 확정) 이다. 매니페스트의 상세(화면별 컴포넌트·문구·아이콘, 컴포넌트, 내비게이션, state, 모델·문구·아이콘·토큰 요약)는 `import_design` 마다 `design.write_manifest` 가 다시 채운다 — 사람이 정한 화면 목록과 (`components_confirmed` 일 때) 컴포넌트의 id·type·title 만 보존한다. 시각 같은 비결정 값은 넣지 않는다 (지문에 들기 때문). 오버레이(sheet·modal·popover)는 화면이 아니라 컴포넌트다 — 사람이 같은 anchor 를 `screens=` 로 올리면 화면이 이긴다.

### 하나를 바꾸면 같이 바뀌어야 하는 것

- **검사 항목 추가/변경** (`checks.py`): 착수 프롬프트 체크리스트 · `precheck` · `verify` 가 전부 `checks.items` + `score.evaluate_role` 을 공유한다. 규칙 문장은 `reports.RULES` 한 곳에만 두고 ID(`W1` `C2` `S1`…)로 인용한다. 서버 거부 메시지도 같은 ID 를 앞에 단다.
- **생성 상수 추가** (`gen.py`): `ApiRoutes` `Screens` `DesignTokens` `Strings` `Icons` 는 "앱이 부르는 이름 = 검사가 세는 이름"이다. 새 상수를 만들면 `checks.targets/items` 에 소비 항목(`API-xx` `SCR-xx` `ICN-xx`…)을 같이 넣어야 검사가 센다. 같은 입력이면 같은 바이트여야 한다 (`gen.drift` 가 바이트 대조).
- **보호 구역·민감 파일 패턴**: `hooks/guard.py` 와 `server/handoff/leaks.py` 가 각각 따로 든다 (훅은 서버를 import 하지 못하므로 의도된 중복). 한쪽을 고치면 다른 쪽도 맞춘다.
- **설정 기본값** 은 `util.DEFAULTS` (역할 경로 `roles` · 점수 가중치·임계치 · `verify.commands` · `test_globs`). 대상 레포의 `.handoff/config.json` 이 덮어쓴다.

### 언어 규칙

에이전트가 읽는 것(착수 프롬프트 · `reports.RULES` · `agents/*.md` · 리포트 응답)은 **영어 명령문**, 사람이 읽는 것(채팅 요약 표 `reports.summary` · 체크리스트 `reports.checklist` · `docs/handoff-*` · 거부 메시지 · README · 스킬이 사용자에게 하는 말)은 **한국어**다. 대시보드 HTML 은 없다 — 사람 판단 지점 앞에 서버가 md 표를 주고 스킬이 채팅에 그대로 보인다. 사람용 문서는 손으로 쓰지 않고 `reports.py` 가 데이터에서 렌더링한다.

### 플러그인 배선

`.claude-plugin/plugin.json` · `.mcp.json`(`run.py serve`) · `hooks/hooks.json`(PreToolUse → `guard.py`, 실패는 통과) · `skills/handoff`(유일한 진입 스킬) · `skills/claude-design`(웹 제품 연결 통로 안내) · `agents/*-builder.md`(구현 서브에이전트, `build` 의 프롬프트를 그대로 받는다 — 본문이 플랫폼 번역 플레이북이다: HTML/CSS→SwiftUI·Compose 매핑, UIKit·View 로 내려가는 기준, 프로젝트 구조별 규칙, 스토어 제출 필수 항목, 빌드·스크린샷 명령. 영어 명령문).
