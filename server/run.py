#!/usr/bin/env python3
"""handoff 서버 런처 + 사람용 CLI.

  python3 run.py                          # MCP 서버 (stdio) — .mcp.json 이 이것을 부른다
  python3 run.py run      --root <레포>    # 그래프를 다음 멈춤까지 돌린다 — 승인은 tty, 과제는 화면에 찍고 멈춘다
  python3 run.py submit <payload.json> --root <레포>   # 과제의 답을 내고 이어 돌린다
  python3 run.py status   --root <레포>
  python3 run.py setup    --root <레포>
  python3 run.py review   --root <레포>    # 계약 확정 (tty 필수)
  python3 run.py ship     --root <레포>    # 완료 승인 (tty 필수)
  python3 run.py unbundle <standalone.html> <폴더>       # Claude Design standalone HTML 내보내기를 파일들로 펼친다

review · ship 은 elicitation 미지원 클라이언트의 폴백이다 — tty 에서만 받는다. run 도 승인 지점에서는 같은 tty 승인이다.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")


def _have_deps():
    try:
        import langgraph  # noqa: F401
        import mcp  # noqa: F401
        return True
    except ImportError:
        return False


def bootstrap_venv():
    """의존성(requirements.txt)이 없으면 server/.venv 를 만들어 갈아탄다. 안내는 stderr 로 (stdout 은 JSON-RPC 통로)."""
    if _have_deps():
        return
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    venv = os.path.join(here, ".venv")
    py = os.path.join(venv, "bin", "python3")
    if os.environ.get("HANDOFF_BOOTSTRAPPED"):
        # venv 안인데도 없다 — 기존 venv 에 새 핀이 추가된 경우. 설치하고 한 번 더 본다.
        print("server/.venv 에 빠진 의존성이 있다 — requirements.txt 를 설치한다.", file=sys.stderr)
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", REQUIREMENTS], check=False, stdout=sys.stderr.fileno())
        if _have_deps():
            return
        print("의존성을 못 찾았고 재기동도 실패했다 — server/.venv 를 지우고 다시 띄운다.", file=sys.stderr)
        raise SystemExit(1)
    if sys.version_info < (3, 10):
        print("Python 3.10 이상이 필요하다.", file=sys.stderr)
        raise SystemExit(1)
    if not os.path.isfile(py):
        print("의존성이 없다 — server/.venv 를 만든다 (최초 1회).", file=sys.stderr)
        try:
            subprocess.run([sys.executable, "-m", "venv", venv], check=True, stdout=sys.stderr.fileno())
            subprocess.run([py, "-m", "pip", "install", "-q", "-r", REQUIREMENTS], check=True, stdout=sys.stderr.fileno())
        except (subprocess.CalledProcessError, OSError) as e:
            print(f"venv 생성 실패: {e}", file=sys.stderr)
            raise SystemExit(1)
    os.environ["HANDOFF_BOOTSTRAPPED"] = "1"
    os.execv(py, [py, os.path.abspath(__file__), *sys.argv[1:]])


def tty_approver(message, items):
    if not sys.stdin.isatty():
        print("승인은 tty 에서만 받는다 — 사람이 터미널에서 직접 실행한다.", file=sys.stderr)
        raise SystemExit(3)
    print("\n" + message + "\n")
    ans = input("승인하시겠습니까? [y/N] ").strip().lower()
    if ans in ("y", "yes"):
        return {"approved": True, "reason": ""}
    return {"approved": False, "reason": input("반려/보류 사유: ").strip()}


def _print_graph(out):
    """run · submit 의 출력 — 과제면 무엇을 낼지, 승인이면 프롬프트, 끝이면 단계."""
    p = out.get("pending")
    if p:
        print(out.get("message", ""))
        for k in ("candidates", "remaining", "hints", "roles", "worktrees", "screens", "components"):
            if p.get(k):
                print(f"\n{k}: " + json.dumps(p[k], ensure_ascii=False, indent=2)[:4000])
        if p.get("prompts"):
            for role, text in p["prompts"].items():
                print(f"\n── kickoff prompt: {role} ──\n{text}")
        print(f"\n다음: python3 {os.path.abspath(__file__)} submit <payload.json> --root <레포>")
        return
    print(out.get("message") or json.dumps(out, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(description="handoff MCP 서버 · 사람용 CLI")
    ap.add_argument("cmd", nargs="?", default="serve",
                    choices=["serve", "run", "submit", "status", "setup", "review", "ship", "unbundle"])
    ap.add_argument("args", nargs="*", help="unbundle: <standalone.html> <폴더> · submit: <payload.json>")
    ap.add_argument("--root", default=None)
    args = ap.parse_args()
    root = os.path.abspath(args.root or os.environ.get("HANDOFF_ROOT") or os.getcwd())

    if args.cmd == "serve":
        os.environ.setdefault("HANDOFF_ROOT", root)
        bootstrap_venv()
        from handoff import server
        server.main()
        return 0

    if args.cmd == "unbundle":
        from handoff import design
        if len(args.args) != 2:
            print("unbundle <standalone.html> <폴더>", file=sys.stderr)
            return 2
        src, dest = args.args
        if not design.is_bundled_html(src):
            print(f"번들 html 이 아니다 (__bundler/manifest 없음): {src}", file=sys.stderr)
            return 1
        os.makedirs(dest, exist_ok=True)
        written = design.unbundle(src, dest)
        for rel in sorted(written.values()):
            print(" ", rel)
        print(f"펼침: {len(written)}개 + {design.stem_of(src)}.dc.html → {dest}")
        return 0

    from handoff import leaks, tools
    if args.cmd in ("run", "submit"):
        bootstrap_venv()
        from handoff import graph
        if args.cmd == "submit":
            if len(args.args) != 1:
                print("submit <payload.json>", file=sys.stderr)
                return 2
            with open(args.args[0], encoding="utf-8") as f:
                out = graph.submit(root, json.load(f))
        else:
            out = graph.advance(root)
        if out.get("pending_human"):
            out = graph.advance(root, approver=tty_approver)
        out = leaks.mask_all_deep(out)
        _print_graph(out)
        return 0 if out.get("ok") else 1
    if args.cmd == "setup":
        out = tools.setup(root)
    elif args.cmd == "status":
        out = tools.status(root)
        if out.get("ok"):
            for s in out["steps"]:
                print(("→" if s["current"] else "✓" if s["passed"] else "·"), s["label"])
            for w in out["warnings"]:
                print("!", w)
            for k in ("summary", "checklist"):
                if out.get(k):
                    print("\n" + out[k]["markdown"])
    elif args.cmd == "review":
        out = tools.review(root, approver=tty_approver)
    else:
        out = tools.ship(root, approver=tty_approver)
    out = leaks.mask_all_deep(out)
    print(out.get("message") or json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
