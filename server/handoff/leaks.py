"""시크릿 · 민감 파일 — 패턴은 경보용이다. 블랙리스트는 진다; 그래도 없는 것보단 낫다."""
import fnmatch
import os
import re

SENSITIVE_GLOBS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p8", "*.p12", "*.pfx", "*.keystore", "*.jks",
    "*.tfstate", "*.tfvars", "*credentials*.json", "*service-account*.json",
    "id_rsa", "id_ed25519", "*.mobileprovision", "secrets.yaml", "secrets.yml", "secrets.json",
)
SENSITIVE_DIR_RE = re.compile(r"(^|/)(\.ssh|\.aws|\.kube|\.gnupg|\.docker)(/|$)")
EXAMPLE_RE = re.compile(r"\.(example|sample|template|dist)$", re.I)

SECRET_RES = [
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("stripe-key", re.compile(r"\b[sr]k_(live|test)_[0-9A-Za-z]{16,}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("assignment", re.compile(
        r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|"
        r"password|passwd|db[_-]?password)\b\s*[:=]\s*['\"]([^'\"\s]{12,})['\"]")),
    ("connection-string", re.compile(r"\b\w+://[^:/\s]+:[^@/\s]{6,}@[^\s'\"]+")),
]
_PLACEHOLDER = re.compile(r"(?i)^(your|my|xxx|example|placeholder|change|replace|dummy|test|<)")
MASK = "•••"


def find(text):
    """[(규칙, 매치 문자열)] — 자리표시 값은 걸지 않는다."""
    hits = []
    for name, rx in SECRET_RES:
        for m in rx.finditer(text or ""):
            val = m.group(2) if name == "assignment" else m.group(0)
            if name == "assignment" and (_PLACEHOLDER.match(val) or "${" in val or val.isupper()):
                continue
            hits.append((name, val))
    return hits


def mask(text):
    if not isinstance(text, str):
        return text
    out = text
    for name, rx in SECRET_RES:
        if name == "assignment":
            out = rx.sub(lambda m: m.group(0).replace(m.group(2), MASK), out)
        else:
            out = rx.sub(MASK, out)
    return out


def mask_deep(obj):
    if isinstance(obj, str):
        return mask(obj)
    if isinstance(obj, dict):
        return {k: mask_deep(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [mask_deep(v) for v in obj]
    return obj


def is_sensitive_file(rel):
    rel = rel.replace(os.sep, "/")
    name = rel.rsplit("/", 1)[-1]
    if EXAMPLE_RE.search(name):
        return False
    if SENSITIVE_DIR_RE.search(rel):
        return True
    return any(fnmatch.fnmatch(name, g) for g in SENSITIVE_GLOBS)
