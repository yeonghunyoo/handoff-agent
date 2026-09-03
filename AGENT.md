# 원칙 — 이 리포가 지키는 것

Claude Design 핸드오프 패키지를 계약으로 받아 iOS · Android · backend 를 병렬로 만들고, 서버가 실측한 것만
사람 승인 뒤 본선에 올린다. 진입은 `/handoff` 하나, 단계는 MCP 서버가 강제한다.

```
import → spec → api → review ✋ → build → verify ⇄ loop → ship ✋ → done
```

## 1. 디자인은 밖에서 끝난다

디자인 루프는 Claude Design 에서 돈다. 이 도구는 내보낸 패키지를 **읽기 전용 정본**(`design/`)으로 받을 뿐,
고치지 않는다. 화면 HTML 이 레이아웃·문구·상태의 정본이고 에이전트가 직접 읽는다 — 중간 표현(레이아웃 트리·
부품 카탈로그)을 두지 않는다. 그런 표현은 정본과 반드시 갈린다.

패키지 포맷은 고정돼 있지 않으므로 인제스트(`design.py`)는 발견 규칙이고 관대하다. 매니페스트는 저장하지
않는다 — `design/` 에서 매번 같은 답이 나온다(결정적). 잠금은 트리 해시로 잡는다.

## 2. 사람이 정하는 것과 서버가 재는 것

사람: 스택 · 인프라(결정만) · 계약 확정 · 완료 승인. 승인 채널은 elicitation 과 tty 둘뿐이고 승인 도구에
approve 류 인자는 없다 — 에이전트가 인자에 넣을 수 있는 것은 게이트가 아니다.

서버: 계약 소비(`API-xx` `SCR-xx`) · 하드코딩 · iOS↔Android 파리티 · 테스트 우회/출처/계약 접촉 · 시크릿(코드·
민감 파일·커밋 이력) · 보호 구역 변경 · 담당 밖 쓰기. 리포트의 자기신고는 참고 자료다. 판정 근거는 잰 것뿐이다.

## 3. 저장값을 믿지 않는다

`state.json` 은 서버만 쓰지만 그래도 실측과 대조한다: 잠금 지문 ≠ 현재 지문이면 review 로 강등한다. `ship` 은
직전 verify 결과를 쓰지 않고 다시 잰다. 리포트에 박힌 차수·해시는 서버가 채운다.

## 4. 에이전트가 아는 기준 = 게이트가 재는 기준

착수 프롬프트의 체크리스트 · `precheck` · `verify` 가 같은 함수(`checks.items` · `score.evaluate_role`)를 쓴다.
루프는 본선이 아니라 워크트리 안(`precheck`)에서 먼저 돈다. 규칙은 프롬프트 한 곳(`reports.RULES`)에만 살고
ID(`W1` `C2` `S1`…)로 인용된다. 서버 거부 메시지는 같은 ID 를 앞에 단다.

## 5. 훅은 경보, 보장은 머지 게이트

워크트리 안에서는 아무것도 막지 않는다. 본선으로 가는 길은 `ship`(재검사 + 사람 승인 + 서버 merge) 하나다.
훅(`hooks/guard.py`)은 낭비를 즉시 끊는 조기 경보라 실패는 통과이고, 서버 코드를 import 하지 않는다.

## 6. 에이전트에게는 영어 명령문, 사람에게는 한국어

착수 프롬프트·리포트 응답·에이전트 정의는 영어 명령문이다. 산문·근거는 컨텍스트만 먹는다. 사람이 읽는
것(대시보드·docs·README·거부 메시지)은 한국어다. 사람용 문서는 손으로 쓰지 않고 기계용 데이터에서 렌더링한다.

## 구성

| 위치 | 역할 |
|---|---|
| `server/handoff/util.py` | 경로 · 설정 · 상태 · 해시 |
| `server/handoff/design.py` | 패키지 인제스트 (발견 규칙) |
| `server/handoff/api.py` | YAML 부분집합 파서 · 라우트 |
| `server/handoff/gen.py` | `ApiRoutes` `Screens` `DesignTokens` 생성 · 드리프트 |
| `server/handoff/checks.py` | 실측 (소비 · 하드코딩 · 파리티 · 위반 · 테스트 · 시크릿) |
| `server/handoff/score.py` | 판정 · 예외 항목 · 인계 |
| `server/handoff/flow.py` | 단계 · 실측 대조 |
| `server/handoff/infra.py` | 인프라 후보 카탈로그 (규모 · 조합 · 요금 페이지 출처) — 요금은 스킬이 매번 새로 읽어 `infra.pricing` 으로 저장, 내장 구간은 폴백. 고르는 것은 사람 |
| `server/handoff/reports.py` | 착수 프롬프트 · md · 대시보드 · 화면 대조 |
| `server/handoff/tools.py` | 도구 (MCP 무관) |
| `server/handoff/server.py` | MCP 계층 — `mcp` 를 import 하는 유일한 파일 |
| `hooks/guard.py` | PreToolUse 경보 (stdlib) |
| `agents/*-builder.md` · `skills/handoff/` | 구현 에이전트 정의(본문 = 플랫폼 번역 플레이북 · 스토어 제출 규칙) · 진입 스킬 |
| `tests/` | 임시 픽스처로 도구 자신을 검사 |

## 금지

- 승인 도구에 approve·yes·force 류 인자 · 사람 대신 승인
- 리포트·저장값을 판정에 쓰기 (다시 잰다)
- 디자인 중간 표현 만들기 (정본은 `design/*.html`)
- `server/` 밖에서 `mcp` import · 훅에서 서버 import
- 침묵하는 검사 (못 잰 것은 "못 잼"으로 보인다)
- 사람용 문서를 손으로 쓰기

## 검증

`bash tests/run.sh` — 서버·훅을 고쳤으면 전체. 문서만 고쳤으면 돌릴 이유가 없다.
