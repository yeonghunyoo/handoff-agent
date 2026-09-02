#!/usr/bin/env python3
"""PreToolUse 경보 훅 — 보호 구역 쓰기 · 민감 파일 · 승인 명령 자동 실행을 끊는다.

보장은 서버의 검사(verify)와 머지 게이트다. 훅은 낭비를 즉시 끊는 조기 경보라서, 판단이 안 서면 통과시킨다
(실패는 통과). stdlib 만 쓰고, 서버 코드를 import 하지 않는다 — 훅이 서버에 매이면 서버가 깨질 때 입력이 막힌다.
"""
import fnmatch
import json
import os
import re
import sys

HO = ".handoff"
PROTECTED = ("design/", "api/", "shared/generated/")
SENSITIVE = (".env", ".env.*", "*.pem", "*.key", "*.p8", "*.p12", "*.pfx", "*.keystore", "*.jks", "*.tfstate",
             "*.tfvars", "*credentials*.json", "*service-account*.json", "id_rsa", "id_ed25519", "secrets.json",
             "secrets.yaml", "secrets.yml")
SENSITIVE_DIR = re.compile(r"(^|/)(\.ssh|\.aws|\.kube|\.gnupg)(/|$)")
EXAMPLE = re.compile(r"\.(example|sample|template|dist)$", re.I)
APPROVE_CMD = re.compile(r"run\.py\b[^|;&\n]*\b(review|ship)\b")
MUTATORS = re.compile(r"(^|[\s;&|(])(rm|mv|cp|tee|sed\s+-i|truncate|touch|mkdir|unzip|tar|git\s+(checkout|restore|rm|mv|reset|clean))\b")
REDIRECT = re.compile(r">{1,2}\s*([^\s;&|]+)")

GUIDE = {
    "design": "design/ 는 디자인 정본이라 읽기 전용이다 — 바꾸려면 새 패키지를 import_design 으로 가져온다.",
    "api": "api/ 는 계약이라 읽기 전용이다 — 바꾸려면 back(to='api') 로 돌아가 api_submit 으로 다시 낸다 (재승인).",
    "shared/generated": "shared/generated/ 는 서버 생성물이다 — 손대면 검사가 [C2] 로 잡는다. 소비만 한다.",
    "state": ".handoff/ 는 도구 상태다 — 서버만 쓴다.",
    "sensitive": "민감 파일이다 — 읽지도 쓰지도 않는다. 값이 필요하면 <이름>.example 을 자리표시로 만들고 사람에게 채울 곳을 안내한다.",
    "approve": "승인은 사람만 한다 — 에이전트가 run.py review|ship 을 실행할 수 없다. elicitation 이나 사람의 터미널 입력을 기다린다.",
}


def find_root(start):
    d = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(d, HO, "config.json")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def rel_of(root, path, cwd):
    p = path if os.path.isabs(path) else os.path.join(cwd, path)
    p = os.path.normpath(p)
    try:
        rel = os.path.relpath(p, root).replace(os.sep, "/")
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    # 워크트리 안이면 워크트리 기준 경로로
    m = re.match(rf"{re.escape(HO)}/worktrees/[^/]+/(.*)$", rel)
    return m.group(1) if m else rel


def is_sensitive(rel):
    name = rel.rsplit("/", 1)[-1]
    if EXAMPLE.search(name):
        return False
    return bool(SENSITIVE_DIR.search(rel)) or any(fnmatch.fnmatch(name, g) for g in SENSITIVE)


def protected_kind(rel):
    if rel.startswith(HO + "/") or rel == HO:
        return "state"
    for p in PROTECTED:
        if rel.startswith(p) or rel == p.rstrip("/"):
            return p.rstrip("/").split("/")[0] if p != "shared/generated/" else "shared/generated"
    return None


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}, ensure_ascii=False))
    sys.exit(0)


def main():
    data = json.load(sys.stdin)
    tool = data.get("tool_name", "")
    inp = data.get("tool_input") or {}
    cwd = data.get("cwd") or os.getcwd()
    root = find_root(cwd) or find_root(os.environ.get("CLAUDE_PROJECT_DIR") or cwd)
    if not root:
        return
    if tool == "Bash":
        cmd = inp.get("command", "")
        if APPROVE_CMD.search(cmd):
            deny(GUIDE["approve"])
        tokens = re.findall(r"[\w./~${}-]+", cmd)
        for tk in tokens:
            rel = rel_of(root, os.path.expanduser(tk), cwd)
            if rel and is_sensitive(rel):
                deny(GUIDE["sensitive"] + f" ({rel})")
        writes = [m.group(1) for m in REDIRECT.finditer(cmd)]
        if MUTATORS.search(cmd):
            writes += tokens
        for tk in writes:
            rel = rel_of(root, os.path.expanduser(tk), cwd)
            kind = protected_kind(rel) if rel else None
            if kind:
                deny(GUIDE[kind] + f" ({rel})")
        return
    path = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
    if not path:
        return
    rel = rel_of(root, path, cwd)
    if not rel:
        return
    if is_sensitive(rel):
        deny(GUIDE["sensitive"] + f" ({rel})")
    if tool == "Read":
        return
    kind = protected_kind(rel)
    if kind:
        deny(GUIDE[kind] + f" ({rel})")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
