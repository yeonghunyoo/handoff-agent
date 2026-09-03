"""시크릿 · 개인정보 · 민감 파일 — 패턴은 경보용이다. 블랙리스트는 진다; 그래도 없는 것보단 낫다.

  find / mask / mask_deep          시크릿 값 (키 · 토큰 · 개인키 · 접속 문자열 · 비밀번호 대입)
  find_pii / mask_all / mask_all_deep  + 개인정보 (이메일 · 휴대전화 · 주민번호 · 카드번호). 채팅으로 나가는 것은 전부 이것을 거친다
  is_sensitive_file                민감 파일 이름 (hooks/guard.py 의 SENSITIVE 와 같은 목록 — 훅은 서버를 import 못 하므로 의도된 중복)
  sanitize_tree(dir)               가져온 패키지에서 민감 파일을 지우고 텍스트의 시크릿을, chats/·README 의 개인정보를 마스킹한다
"""
import fnmatch
import os
import re

SENSITIVE_GLOBS = (
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p8", "*.p12", "*.pfx", "*.keystore", "*.jks",
    "*.tfstate", "*.tfvars", "*credentials*.json", "*service-account*.json",
    "id_rsa", "id_ed25519", "id_ecdsa", "*.mobileprovision", "secrets.yaml", "secrets.yml", "secrets.json",
    ".netrc", ".npmrc", ".pypirc", ".git-credentials", ".htpasswd", "*.secret", "*.gpg", "*.asc", "*.ovpn", "*.kdbx",
)
SENSITIVE_DIR_RE = re.compile(r"(^|/)(\.ssh|\.aws|\.kube|\.gnupg|\.docker|\.azure|\.config/gcloud)(/|$)")
# 가져온 패키지에서 개인정보까지 마스킹하는 곳 — 대화 기록과 README 는 사람이 쓴 글이다. 화면 HTML 의 예시 데이터는 건드리지 않는다
PII_SCOPE_RE = re.compile(r"(^|/)(chats/|README\.md$|readme\.md$)")
TEXT_EXT = (".html", ".htm", ".md", ".json", ".css", ".js", ".jsx", ".ts", ".tsx", ".txt", ".svg", ".yaml", ".yml", ".csv")
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

# 개인정보 — 예시 도메인·번호는 남긴다 (디자인 목업의 자리표시)
_PII_EXEMPT_EMAIL = re.compile(r"(?i)@(example\.(com|org|net)|test\.com|localhost|invalid|email\.com|domain\.com)$|^(noreply|no-reply|hello|info|support|contact)@")
PII_RES = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("kr-rrn", re.compile(r"\b\d{6}-[1-8]\d{6}\b")),                                   # 주민등록번호
    ("kr-mobile", re.compile(r"(?<![\d-])(?:\+82[- ]?1|01)[016789][- ]?\d{3,4}[- ]?\d{4}(?![\d-])")),
    ("card", re.compile(r"(?<!\d)(?:\d{4}[- ]){3}\d{4}(?!\d)")),
]
_PII_EXEMPT_NUM = re.compile(r"^(?:\+82[- ]?1|01)[016789][- ]?(?:0000|1234|1111|9999)[- ]?(?:0000|1234|5678|1111|9999)$")


def _pii_exempt(name, val):
    if name == "email":
        return bool(_PII_EXEMPT_EMAIL.search(val))
    if name == "kr-mobile":
        return bool(_PII_EXEMPT_NUM.match(val))
    if name == "card":
        digits = val.replace("-", "").replace(" ", "")
        return len(set(digits)) <= 2 or digits.startswith(("4111111111111111", "1234", "0000"))
    return False


def find_pii(text):
    """[(종류, 값)] — 예시 값은 걸지 않는다."""
    hits = []
    for name, rx in PII_RES:
        for m in rx.finditer(text or ""):
            if not _pii_exempt(name, m.group(0)):
                hits.append((name, m.group(0)))
    return hits


def mask_pii(text):
    if not isinstance(text, str):
        return text
    out = text
    for name, rx in PII_RES:
        out = rx.sub(lambda m: m.group(0) if _pii_exempt(name, m.group(0)) else MASK, out)
    return out


def mask_all(text):
    """시크릿 + 개인정보. 채팅·상태·문서로 나가는 문자열은 전부 이것을 거친다."""
    return mask_pii(mask(text))


def mask_all_deep(obj):
    if isinstance(obj, str):
        return mask_all(obj)
    if isinstance(obj, dict):
        return {k: mask_all_deep(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [mask_all_deep(v) for v in obj]
    return obj


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


def sanitize_tree(d):
    """가져온 패키지(design/)를 정리한다 — 되돌릴 수 없는 쪽으로만. 원본 zip 은 건드리지 않는다.
    · 민감 파일(is_sensitive_file)은 지운다 (디자인에 있을 이유가 없다)
    · 텍스트 파일의 시크릿 값은 마스킹한다
    · chats/ · README 의 개인정보는 마스킹한다 (사람이 쓴 글 — 착수 프롬프트·힌트로 흘러간다)
    반환: {"dropped": [rel...], "masked": {rel: n}, "secrets": n, "pii": n}"""
    rep = {"dropped": [], "masked": {}, "secrets": 0, "pii": 0}
    for base, dirs, files in os.walk(d):
        for f in files:
            fp = os.path.join(base, f)
            rel = os.path.relpath(fp, d).replace(os.sep, "/")
            if is_sensitive_file(rel):
                os.remove(fp)
                rep["dropped"].append(rel)
                continue
            if not f.lower().endswith(TEXT_EXT):
                continue
            try:
                with open(fp, encoding="utf-8") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            n_s = len(find(text))
            n_p = len(find_pii(text)) if PII_SCOPE_RE.search(rel) else 0
            if not (n_s or n_p):
                continue
            out = mask(text)
            if n_p:
                out = mask_pii(out)
            with open(fp, "w", encoding="utf-8") as fh:
                fh.write(out)
            rep["masked"][rel] = n_s + n_p
            rep["secrets"] += n_s
            rep["pii"] += n_p
    rep["dropped"].sort()
    return rep
