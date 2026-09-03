#!/usr/bin/env python3
"""handoff 서버 런처 + 사람용 CLI.

  python3 run.py                          # MCP 서버 (stdio) — .mcp.json 이 이것을 부른다
  python3 run.py status   --root <레포>
  python3 run.py setup    --root <레포>
  python3 run.py review   --root <레포>    # 계약 확정 (tty 필수)
  python3 run.py ship     --root <레포>    # 완료 승인 (tty 필수)
  python3 run.py unbundle <standalone.html> <폴더>       # Claude Design standalone HTML 내보내기를 파일들로 펼친다

review · ship 은 elicitation 미지원 클라이언트의 폴백이다 — tty 에서만 받는다.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
MCP_PIN = "mcp==2.1.1"


def bootstrap_venv():
    """mcp 가 없으면 server/.venv 를 만들어 갈아탄다. 안내는 stderr 로 (stdout 은 JSON-RPC 통로)."""
    try:
        import mcp  # noqa: F401
        return
    except ImportError:
        pass
    if os.environ.get("HANDOFF_BOOTSTRAPPED"):
        print("mcp 를 못 찾았고 재기동도 실패했다 — server/.venv 를 지우고 다시 띄운다.", file=sys.stderr)
        raise SystemExit(1)
    if sys.version_info < (3, 10):
        print("Python 3.10 이상이 필요하다.", file=sys.stderr)
        raise SystemExit(1)
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    venv = os.path.join(here, ".venv")
    py = os.path.join(venv, "bin", "python3")
    if not os.path.isfile(py):
        print("mcp 가 없다 — server/.venv 를 만든다 (최초 1회).", file=sys.stderr)
        try:
            subprocess.run([sys.executable, "-m", "venv", venv], check=True, stdout=sys.stderr.fileno())
            subprocess.run([py, "-m", "pip", "install", "-q", MCP_PIN], check=True, stdout=sys.stderr.fileno())
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


def main():
    ap = argparse.ArgumentParser(description="handoff MCP 서버 · 사람용 CLI")
    ap.add_argument("cmd", nargs="?", default="serve",
                    choices=["serve", "status", "setup", "review", "ship", "unbundle"])
    ap.add_argument("args", nargs="*", help="unbundle: <standalone.html> <폴더>")
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

    from handoff import tools
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
    from handoff import leaks
    out = leaks.mask_all_deep(out)
    print(out.get("message") or json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
