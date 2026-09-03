"""MCP 계층 — `mcp` 를 import 하는 유일한 파일. 구현은 tools.py 에 있다.

review · ship 은 elicitation(클라이언트가 사람에게 직접 띄우는 입력)으로만 통과한다. 미지원 클라이언트면
자동 승인으로 강등하지 않고 사람이 터미널에서 칠 명령을 돌려준다.
"""
import os

from mcp.server.mcpserver import Context, MCPServer
from pydantic import BaseModel

from . import tools

ROOT = os.path.abspath(os.environ.get("HANDOFF_ROOT") or os.getcwd())

mcp = MCPServer(
    "handoff",
    instructions=("Claude Design 핸드오프 패키지로 iOS · Android · backend 를 한 번에 만드는 워크플로 서버. "
                  "순서는 status 가 안내한다: 패키지 등록 → 스펙 → openapi → 계약 확정(사람) → 구현 → 검사 → "
                  "완료 승인(사람). 뒤로 가려면 back."),
)


class _Approval(BaseModel):
    approved: bool
    reason: str = ""


async def _elicit(ctx, message):
    try:
        r = await ctx.elicit(message=message, schema=_Approval)
    except Exception:
        return None
    if getattr(r, "action", None) != "accept" or getattr(r, "data", None) is None:
        return {"approved": False, "reason": "사람이 취소/거절했다"}
    return {"approved": r.data.approved, "reason": r.data.reason}


async def _approve_then(ctx, run):
    pre = run(None)
    if not pre.get("pending_human"):
        return pre
    decision = await _elicit(ctx, pre["approval_prompt"])
    if decision is None:
        return pre
    return run(lambda m, i: decision)


@mcp.tool()
def status() -> dict:
    """현재 단계 · 다음 행동 · 경고. 무엇을 할지 모르면 항상 이것부터."""
    return tools.status(ROOT)


@mcp.tool()
def setup() -> dict:
    """미배선 레포를 배선한다 (.handoff/config.json · .gitignore · CLAUDE.md 절). 멱등."""
    return tools.setup(ROOT)


@mcp.tool()
def import_design(path: str = "", screens: list | None = None, components: list | None = None) -> dict:
    """① Claude Design 산출물을 design/ 로 가져오고 화면·컴포넌트·토큰·문서를 발견한다. path: 핸드오프 zip/tar.gz ·
    standalone HTML 내보내기(번들을 펼친다) · 폴더. 응답의 화면 후보와 컴포넌트(타입: sheet·modal·popover·tab·
    button·toggle·input·slider·item·gesture) 목록을 사용자에게 보여 확정받고, 고칠 게 있으면 path 없이
    screens=[{id,title,file,anchor}] · components=[{id,type,title,anchor}] 로 다시 부른다. 확정되면
    design/handoff.manifest.json 에 화면·컴포넌트·내비게이션·state·모델·문구·아이콘·토큰 상세가 실린다.
    done 상태에서 부르면 새 사이클을 연다."""
    return tools.import_design(ROOT, path or None, screens, components)


@mcp.tool()
def spec_save(spec: dict) -> dict:
    """② 스펙 저장. 필수: platforms[ios|android], stack.backend, infra.scale(small|medium_plus — infra.mau/dau 숫자를 주면
    서버가 정한다), infra.db/auth/hosting (결정만 기록 — status.infra_options 의 조합 id 를 infra.combo 로 주면 db/auth/hosting/cost 가 채워진다).
    infra.pricing {checked: YYYY-MM-DD, combos: {id: {small, medium_plus, sources[]}}} 는 그날 요금 페이지에서 읽은 값 — 표를 보이기 전에 저장한다.
    선택: stack.ios_project, stack.android_project, infra.mau, infra.dau, infra.combo, infra.cost, infra.env_vars[], infra.notes,
    divergences[] (승인된 플랫폼 차이 주제). 필수가 다 차기 전까지 부분 저장으로 누적된다 — 답을 받을 때마다 그 항목만 넘겨도 된다."""
    return tools.spec_save(ROOT, spec)


@mcp.tool()
def api_submit(openapi: str) -> dict:
    """③ api/openapi.yaml 본문 제출. design/ 의 화면·문서에서 필요한 데이터를 읽어 초안한다. 검증 통과분만 쓴다."""
    return tools.api_submit(ROOT, openapi)


@mcp.tool()
async def review(ctx: Context) -> dict:
    """④ 계약 확정 — 사람 승인(elicitation). 승인하면 design/ + api/ 가 본선에 커밋·잠금되고 구현 단계로 간다."""
    return await _approve_then(ctx, lambda approver: tools.review(ROOT, approver=approver))


@mcp.tool()
def back(to: str, reason: str) -> dict:
    """이전 단계로 복귀. to: import | spec | api | build. 사유는 기록에 남는다."""
    return tools.back(ROOT, to, reason)


@mcp.tool()
def build() -> dict:
    """⑤ 착수 — 역할별 워크트리 + 생성 상수 + 영어 착수 프롬프트. 이미 착수됐으면 이어가기 정보를 준다."""
    return tools.build(ROOT)


@mcp.tool()
def precheck(role: str) -> dict:
    """⑤ 중 자가 점검 — verify 와 같은 검사를 자기 워크트리에 돌린다. report 전 통과가 의무다."""
    return tools.precheck(ROOT, role)


@mcp.tool()
def report(role: str, report: dict) -> dict:
    """⑤ 말 리포트 접수. {status: done|partial|blocked, not_done[], blocked[{what,tried[],error}], divergences[{topic,reason}],
    proposals[], build{ok,seconds}, tests{passed,failed,seconds}, human_check[]}. 마지막 접수가 ⑥ verify 를 자동 실행한다."""
    return tools.report(ROOT, role, report)


@mcp.tool()
def verify() -> dict:
    """⑥ 검사 — 서버가 코드·diff 를 재검사해 점수와 loop/pass 를 판정한다. loop 면 인계가 만들어진다."""
    return tools.verify(ROOT)


@mcp.tool()
async def ship(ctx: Context) -> dict:
    """⑦ 완료 승인 — 재검사 뒤 사람이 elicitation 으로 승인하면(예외 항목 포함) 브랜치를 본선에 머지한다."""
    return await _approve_then(ctx, lambda approver: tools.ship(ROOT, approver=approver))


def main():
    mcp.run(transport="stdio")
