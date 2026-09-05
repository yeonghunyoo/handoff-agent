"""MCP 계층 — 실제 서버를 인메모리 클라이언트로 띄워 elicitation 승인 경로를 검사한다. server/.venv 의 python 으로 돈다."""
import asyncio
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, HERE)

try:
    from mcp.client.session import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams
    from mcp.types import ElicitResult
except ImportError:
    print("mcp 가 없어 MCP 계층 검사를 건너뛴다 — server/.venv 로 실행한다.")
    sys.exit(0)

import cases  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  ok  " if cond else "  FAIL") + " " + name + ("" if cond else f"  — {detail}"))
    if not cond:
        FAILS.append(name)


async def scenario():
    root = cases.make_repo()
    os.environ["HANDOFF_ROOT"] = root
    from handoff import server
    server.ROOT = root
    pkg = cases.make_package(os.path.join(os.path.dirname(root), "pkg"))
    prompts = []
    mode = {"kind": "accept"}                       # 클라이언트 흉내: cancel · decline · accept(폼 제출)

    async def elicit(ctx, params):
        prompts.append(params.message)
        if mode["kind"] in ("cancel", "decline"):   # 창 없는 클라이언트의 자동 응답 (content 없음)
            return ElicitResult(action=mode["kind"])
        approve = len(prompts) != 4                 # 폼 제출: 첫 제출은 반려, 그 다음은 승인
        return ElicitResult(action="accept", content={"approved": approve, "reason": "" if approve else "라우트 빠짐"})

    def rejected():
        with open(os.path.join(root, ".handoff", "state.json")) as f:
            return [h for h in json.load(f)["history"] if h["ev"] == "review_rejected"]

    async with create_client_server_memory_streams() as (cs, ss):
        low = server.mcp._lowlevel_server
        task = asyncio.create_task(low.run(ss[0], ss[1], low.create_initialization_options(), raise_exceptions=True))
        async with ClientSession(cs[0], cs[1], elicitation_callback=elicit) as s:
            await s.initialize()
            names = sorted(t.name for t in (await s.list_tools()).tools)
            check("tools: 12개", names == sorted(["status", "setup", "import_design", "spec_save", "api_submit", "review", "back",
                                                  "build", "precheck", "report", "verify", "ship"]), names)
            schemas = {t.name: json.dumps(getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {})
                       for t in (await s.list_tools()).tools}
            check("승인 도구에 approve 류 인자 없음", all(k not in schemas["review"].lower() and k not in schemas["ship"].lower()
                                                     for k in ("approve", "yes", "force")))

            async def call(name, **args):
                r = await s.call_tool(name, args)
                return json.loads(r.content[0].text)

            r = await call("status")
            check("status: import", r["phase"] == "import", r)
            r = await call("import_design", path=pkg)
            check("import_design", r["ok"] and len(r["screens"]) == 3, r.get("message"))
            r = await call("spec_save", spec=cases.SPEC)
            check("spec_save", r["ok"] and not r.get("draft"), r)
            r = await call("api_submit", openapi=cases.OPENAPI)
            check("api_submit", r["ok"] and len(r["routes"]) == 3, r.get("message"))
            mode["kind"] = "cancel"
            r = await call("review")
            check("review: 자동 cancel → 채널 없음 (반려 기록 안 함)",
                  r.get("pending_human") and r.get("no_channel") and "run.py" in r["message"] and not rejected(), r)
            mode["kind"] = "decline"
            r = await call("review")
            check("review: 즉답 decline → 채널 없음 (반려 기록 안 함)",
                  r.get("pending_human") and "자동 응답" in r.get("no_channel", "") and not rejected(), r)
            server.AUTO_REPLY_SECONDS = 0.0         # 사람이 읽고 누른 decline 흉내
            r = await call("review")
            check("review: 사람 decline → 반려 기록", r["ok"] and r["approved"] is False and len(rejected()) == 1, r)
            server.AUTO_REPLY_SECONDS = 5.0
            mode["kind"] = "accept"
            r = await call("review")
            check("review: 폼 반려 (elicitation)", r["ok"] and r["approved"] is False and len(prompts) == 4
                  and rejected()[-1]["reason"] == "라우트 빠짐", r)
            r = await call("review")
            check("review: 승인 → build", r["ok"] and r["approved"] and r["version"] == 1 and len(prompts) == 5, r)
            check("elicitation 문구에 지문", "지문" in prompts[-1])
            r = await call("build")
            check("build", r["ok"] and len(r["prompts"]) == 3, r.get("message"))
            cases.implement(root)
            for role in ("backend", "ios", "android"):
                r = await call("report", role=role, report=cases.report())
            check("report → verify pass", r["ok"] and r["verify"]["verdict"] == "pass", r.get("verify", {}).get("blockers"))
            r = await call("ship")
            check("ship: 승인 → 머지", r["ok"] and r["approved"] and len(r["merges"]) == 3 and len(prompts) == 6, r)
            r = await call("status")
            check("done", r["phase"] == "done")
        task.cancel()


def main():
    asyncio.run(scenario())
    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}: " + ", ".join(FAILS))
        return 1
    print("mcp ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
