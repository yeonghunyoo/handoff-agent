"""② 스펙 — 인프라 후보 카탈로그. 사람이 고르는 데 쓰는 데이터다. 서버는 정하지 않는다.

  SCALES              규모 구간 — 예상 MAU·DAU 로 정한다 (small | medium_plus)
  COMBOS              db · auth · hosting 조합 + 규모별 월 비용 구간 + 장단점 + 종속성
  scale_of(mau, dau)  숫자 → 규모 id (둘 다 없으면 None)
  SHORTLIST           규모별 후보 4~5개 (id, 추천 순). 표에 보이는 것은 이것뿐이고 나머지는 others 로 id 만 든다
  catalog(scale)      규모가 없으면 조합을 내지 않는다 (규모부터 묻는다). 있으면 SHORTLIST 만 — 싼 순
  expand(infra)       spec.infra 를 채운다: mau/dau → scale, combo → db/auth/hosting/cost (비어 있는 칸만)

  SERVICES            서비스 → 공식 요금 페이지. 인터뷰 때마다 여기서 새로 읽는다
  pricing_problems(p) infra.pricing 모양 검사

순서는 **규모 → 후보 → 요금**이다. 규모가 정해지기 전에는 조합도 요금 페이지 목록도 내지 않는다.
요금은 **매번 새로 가져온다**. 서버는 네트워크를 쓰지 않으므로 스킬(세션의 WebFetch/WebSearch)이 catalog(scale).services
(그 규모 후보가 쓰는 서비스만, 10개 안팎) 의 요금 페이지를 읽어 규모별 월 구간을 다시 잡고 `spec_save({"infra": {"pricing": {...}}})` 로 저장한다. 그 뒤로
catalog · expand · 착수 프롬프트 · 요약 표는 저장된 값(확인일 · 출처 포함)을 쓴다. COMBOS 의 cost 는 조회가
안 될 때만 쓰는 폴백이고 COST_BASIS 가 그 기준 시점이다 — 폴백으로 고른 것은 그렇게 표시된다.
사람이 읽는 칸(label · fit · note)은 한국어, 에이전트가 읽는 칸(en · build)은 영어 명령문이다.
"""
import re

SMALL_MAU = 10_000
SMALL_DAU = 1_000

SCALES = [
    {"id": "small", "label": "소규모", "en": "small",
     "mau": f"≤ {SMALL_MAU:,}", "dau": f"≤ {SMALL_DAU:,}",
     "note": "무료 티어·인스턴스 하나로 충분하다. 관리형 서비스로 운영 부담을 없애는 쪽이 싸다",
     "build": "one instance, one database, one deploy command; no sharding, no cache layer, no queue "
              "unless a route needs it"},
    {"id": "medium_plus", "label": "중규모 이상", "en": "medium or larger",
     "mau": f"> {SMALL_MAU:,}", "dau": f"> {SMALL_DAU:,}",
     "note": "수평 확장·커넥션 풀·캐시·백업·모니터링이 비용에 들어온다. 예약 요금이나 자체 운영이 더 쌀 수 있다",
     "build": "stateless services behind a load balancer, connection pooling, cache the hot reads, "
              "background jobs off the request path, health checks, structured logs, automated backups"},
]

COST_UNIT = "월 · USD · 대략 (무료 티어 포함, 공개 요금 기준)"
COST_BASIS = "2026-09"   # COMBOS.cost 폴백을 잡은 시점

# 서비스 → 공식 요금 페이지. 조합의 db/auth/hosting 이 여기 키를 가리킨다 (같은 서비스는 한 번만 읽으면 된다).
SERVICES = {
    "supabase": "https://supabase.com/pricing",
    "fly": "https://fly.io/pricing/",
    "railway": "https://railway.com/pricing",
    "hetzner": "https://www.hetzner.com/cloud/",
    "lightsail": "https://aws.amazon.com/lightsail/pricing/",
    "firebase": "https://firebase.google.com/pricing",
    "cloud-run": "https://cloud.google.com/run/pricing",
    "cloud-sql": "https://cloud.google.com/sql/pricing",
    "dynamodb": "https://aws.amazon.com/dynamodb/pricing/",
    "cognito": "https://aws.amazon.com/cognito/pricing/",
    "lambda": "https://aws.amazon.com/lambda/pricing/",
    "api-gateway": "https://aws.amazon.com/api-gateway/pricing/",
    "rds": "https://aws.amazon.com/rds/postgresql/pricing/",
    "fargate": "https://aws.amazon.com/fargate/pricing/",
    "neon": "https://neon.com/pricing",
    "clerk": "https://clerk.com/pricing",
    "vercel": "https://vercel.com/pricing",
    "cloudflare-workers": "https://developers.cloudflare.com/workers/platform/pricing/",
    "cloudflare-d1": "https://developers.cloudflare.com/d1/platform/pricing/",
    "mongodb-atlas": "https://www.mongodb.com/pricing",
    "auth0": "https://auth0.com/pricing",
    "render": "https://render.com/pricing",
    "ncp-db": "https://www.ncloud.com/product/database/cloudDbPostgresql",
    "ncp-server": "https://www.ncloud.com/product/compute/server",
    "eks": "https://aws.amazon.com/eks/pricing/",
    "gke": "https://cloud.google.com/kubernetes-engine/pricing",
}

# 조합. scales 는 그 규모에서 추천할 만한 것 (다른 규모라도 고를 수는 있다 — 그래서 cost 는 둘 다 둔다).
# services 는 SERVICES 의 키 — 요금을 새로 읽을 때 이 페이지들을 본다. cost 는 폴백(COST_BASIS 시점).
COMBOS = [
    {"id": "supabase-fly", "services": ['supabase', 'fly'], "label": "Supabase + Fly.io",
     "db": "PostgreSQL (Supabase)", "auth": "Supabase Auth", "hosting": "Fly.io",
     "scales": ["small", "medium_plus"], "lockin": "중",
     "cost": {"small": "$0–30", "medium_plus": "$60–300"},
     "fit": "Postgres·인증·스토리지·실시간이 한 콘솔. 앱 백엔드 기본형. Fly 는 리전 선택이 자유롭다"},
    {"id": "railway", "services": ['railway'], "label": "Railway 올인원",
     "db": "PostgreSQL (Railway)", "auth": "자체 JWT (백엔드 구현)", "hosting": "Railway",
     "scales": ["small"], "lockin": "낮음",
     "cost": {"small": "$5–25", "medium_plus": "$50–200"},
     "fit": "Heroku 식 배포, DB 까지 한 곳. 가장 단순한 시작. 커지면 DB 를 밖으로 빼는 게 보통"},
    {"id": "vps-docker", "services": ['hetzner', 'lightsail'], "label": "VPS + Docker Compose",
     "db": "PostgreSQL (자체 컨테이너)", "auth": "자체 JWT (백엔드 구현)", "hosting": "VPS (Hetzner · Lightsail · Vultr)",
     "scales": ["small", "medium_plus"], "lockin": "낮음",
     "cost": {"small": "$5–20", "medium_plus": "$40–200 + 운영 인력"},
     "fit": "가장 싸고 종속이 없다. 백업·모니터링·보안 패치·장애 대응을 직접 한다"},
    {"id": "firebase", "services": ['firebase', 'cloud-run'], "label": "Firebase",
     "db": "Firestore", "auth": "Firebase Auth", "hosting": "Cloud Run (또는 Cloud Functions)",
     "scales": ["small", "medium_plus"], "lockin": "높음",
     "cost": {"small": "$0–25", "medium_plus": "$100–600"},
     "fit": "모바일 SDK·푸시·분석이 붙어 있다. 문서 DB 라 관계형 조회·집계가 많으면 불리. 읽기·쓰기 횟수 과금이라 트래픽에 민감"},
    {"id": "gcp-cloudrun", "services": ['cloud-sql', 'firebase', 'cloud-run'], "label": "GCP Cloud Run + Cloud SQL",
     "db": "PostgreSQL (Cloud SQL)", "auth": "Firebase Auth", "hosting": "Cloud Run",
     "scales": ["small", "medium_plus"], "lockin": "중",
     "cost": {"small": "$10–50", "medium_plus": "$100–500"},
     "fit": "컨테이너 그대로 올리고 0 까지 스케일. Cloud SQL 최소 인스턴스가 고정비"},
    {"id": "aws-serverless", "services": ['dynamodb', 'cognito', 'lambda', 'api-gateway'], "label": "AWS 서버리스",
     "db": "DynamoDB", "auth": "Cognito", "hosting": "Lambda + API Gateway",
     "scales": ["small", "medium_plus"], "lockin": "높음",
     "cost": {"small": "$0–20", "medium_plus": "$80–500"},
     "fit": "요청량 비례 과금, 유휴 비용 0. 콜드스타트와 DynamoDB 단일 테이블 모델링을 감수한다"},
    {"id": "aws-classic", "services": ['rds', 'cognito', 'fargate'], "label": "AWS 정석 (RDS + ECS)",
     "db": "PostgreSQL (RDS)", "auth": "Cognito", "hosting": "ECS Fargate (+ ALB)",
     "scales": ["medium_plus"], "lockin": "중",
     "cost": {"small": "$60–150", "medium_plus": "$200–1,000+"},
     "fit": "어디서나 운영 인력을 구할 수 있는 구성. 고정비가 있어 소규모엔 비싸다. 규제·감사 요건에 강하다"},
    {"id": "neon-vercel", "services": ['neon', 'clerk', 'vercel'], "label": "Neon + Clerk + Vercel",
     "db": "PostgreSQL (Neon, 서버리스)", "auth": "Clerk", "hosting": "Vercel Functions",
     "scales": ["small", "medium_plus"], "lockin": "중",
     "cost": {"small": "$0–45", "medium_plus": "$100–400"},
     "fit": "웹 대시보드를 같이 낼 때 편하다. Node/TypeScript 백엔드에 맞고, Python 은 함수 제약이 있다"},
    {"id": "cloudflare", "services": ['cloudflare-d1', 'cloudflare-workers'], "label": "Cloudflare Workers + D1",
     "db": "D1 (SQLite)", "auth": "자체 JWT (백엔드 구현)", "hosting": "Cloudflare Workers",
     "scales": ["small"], "lockin": "높음",
     "cost": {"small": "$0–5", "medium_plus": "$25–150"},
     "fit": "엣지에서 가장 싸고 빠르다. D1 은 SQLite 라 대용량·복잡 조인에 한계. Workers 런타임이라 Python 백엔드는 부적합"},
    {"id": "mongo-auth0-render", "services": ['mongodb-atlas', 'auth0', 'render'], "label": "MongoDB Atlas + Auth0 + Render",
     "db": "MongoDB Atlas", "auth": "Auth0", "hosting": "Render",
     "scales": ["small", "medium_plus"], "lockin": "중",
     "cost": {"small": "$0–35", "medium_plus": "$150–800"},
     "fit": "문서 모델 + 소셜·기업 로그인 종합(Auth0). Auth0 은 MAU 과금이라 사용자가 늘면 인증비가 먼저 커진다"},
    {"id": "ncp", "services": ['ncp-db', 'ncp-server'], "label": "네이버클라우드 (NCP)",
     "db": "PostgreSQL (Cloud DB for PostgreSQL)", "auth": "자체 JWT (백엔드 구현)", "hosting": "NCP Server (또는 Cloud Functions)",
     "scales": ["small", "medium_plus"], "lockin": "중",
     "cost": {"small": "$30–80", "medium_plus": "$150–700"},
     "fit": "국내 리전·원화 결제·공공/금융 규제 요건. 관리형 서비스 폭은 좁아 직접 붙일 게 많다"},
    {"id": "k8s-managed", "services": ['rds', 'cloud-sql', 'eks', 'gke'], "label": "관리형 Kubernetes (EKS · GKE)",
     "db": "PostgreSQL (RDS · Cloud SQL)", "auth": "Keycloak (자체 운영) 또는 Cognito", "hosting": "EKS · GKE",
     "scales": ["medium_plus"], "lockin": "낮음",
     "cost": {"small": "$150–300", "medium_plus": "$400–2,000+"},
     "fit": "서비스가 여럿이고 팀에 운영 경험이 있을 때. 클러스터 자체 비용이 있어 소규모엔 과하다"},
]

# 규모별 후보 — 표에 보이는 것은 이 4~5개뿐이다 (추천 순). 나머지 조합은 others 로 id·이름만 들고, 사람이 id 로 부르면 그대로 쓴다.
SHORTLIST = {
    "small": ["supabase-fly", "railway", "vps-docker", "firebase", "aws-serverless"],
    "medium_plus": ["supabase-fly", "gcp-cloudrun", "aws-classic", "aws-serverless", "vps-docker"],
}

_ID = {s["id"]: s for s in SCALES}
_COMBO = {c["id"]: c for c in COMBOS}
assert all(sid in _ID and 4 <= len(ids) <= 5 and all(sid in _COMBO[i]["scales"] for i in ids) for sid, ids in SHORTLIST.items())


def scale(sid):
    return _ID.get(str(sid or "").lower())


def combo(cid):
    return _COMBO.get(str(cid or "").lower())


def _num(v):
    """3000 · '3,000' · '3k' · '1.2m' → int. 못 읽으면 None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = re.fullmatch(r"\s*([\d,_]*\.?\d+)\s*([kKmM]?)\s*", str(v))
    if not m:
        return None
    n = float(m.group(1).replace(",", "").replace("_", ""))
    n *= {"": 1, "k": 1_000, "m": 1_000_000}[m.group(2).lower()]
    return int(n)


def scale_of(mau, dau):
    m, d = _num(mau), _num(dau)
    if m is None and d is None:
        return None
    if (m or 0) > SMALL_MAU or (d or 0) > SMALL_DAU:
        return "medium_plus"
    return "small"


def _cost_low(s):
    m = re.search(r"\$([\d,]+)", s or "")
    return int(m.group(1).replace(",", "")) if m else 10**9


def _fresh(pricing, cid):
    """저장된 요금(infra.pricing.combos[cid]) — 모양이 맞을 때만."""
    if not isinstance(pricing, dict) or not isinstance(pricing.get("combos"), dict):
        return None
    row = pricing["combos"].get(cid)
    return row if isinstance(row, dict) and all(isinstance(row.get(k), str) and row[k] for k in ("small", "medium_plus")) else None


def catalog(sid=None, pricing=None):
    """스킬이 표로 보이는 목록. 규모(sid)가 없으면 combos 는 비어 있다 — 규모부터 묻는다.
    있으면 SHORTLIST[sid] 의 4~5개만 (싼 순) + others (나머지 id·이름). services 는 그 후보가 쓰는 요금 페이지만.
    pricing(infra.pricing) 이 있으면 그 값을 쓰고 확인일·출처를 단다. 없는 조합은 폴백 + cost_basis 로 표시한다."""
    s = scale(sid)
    checked = pricing.get("checked") if isinstance(pricing, dict) else None
    base = {"scale": s["id"] if s else None, "scales": SCALES, "cost_unit": COST_UNIT, "cost_basis": COST_BASIS,
            "cost_checked": checked}
    if not s:
        return {**base, "combos": [], "others": [], "services": {}, "fresh": 0, "stale": 0,
                "note": "규모가 아직 없다 — 예상 MAU·DAU 를 먼저 묻는다 (infra.mau/dau 또는 infra.scale). "
                        "규모가 정해지면 그 규모에 맞는 조합 4~5개와 그 조합이 쓰는 요금 페이지만 여기 실린다. "
                        "규모 전에는 요금 페이지를 읽지 않는다"}
    rows, fresh_n = [], 0
    for cid in SHORTLIST[s["id"]]:
        c = _COMBO[cid]
        row = {k: c[k] for k in ("id", "label", "db", "auth", "hosting", "lockin", "fit", "scales", "services")}
        row["sources"] = [SERVICES[n] for n in c["services"]]
        f = _fresh(pricing, cid)
        if f:
            fresh_n += 1
            row["cost"] = f[s["id"]]
            row["cost_checked"] = checked
            if f.get("sources"):
                row["sources"] = list(f["sources"])
            if f.get("note"):
                row["cost_note"] = f["note"]
        else:
            row["cost"] = c["cost"][s["id"]]
            row["cost_basis"] = COST_BASIS   # 폴백 — 조회가 안 된 것
        row["recommended"] = True
        rows.append(row)
    rows.sort(key=lambda r: _cost_low(r["cost"]))
    services = {n: SERVICES[n] for c in rows for n in c["services"]}
    others = [{"id": c["id"], "label": c["label"]} for c in COMBOS if c["id"] not in SHORTLIST[s["id"]]]
    return {**base, "combos": rows, "others": others, "services": services, "fresh": fresh_n, "stale": len(rows) - fresh_n,
            "note": (f"{s['label']} 후보 {len(rows)}개다. 요금은 매번 새로 읽는다 — services 의 요금 페이지({len(services)}개)만 조회해 "
                     "규모별 월 구간을 다시 잡고 spec_save({infra: {pricing: {checked, combos: {id: {small, medium_plus, sources[], note?}}}}}) "
                     "로 저장한 뒤 표를 보인다. cost_basis 가 달린 행은 아직 폴백이다. 그래도 비교용 대략치다 — 확정 전에 사람이 요금표를 본다. "
                     "others 는 표에 없는 나머지 조합이다 — 사람이 그 id 를 부르면 그대로 쓴다 (요금은 폴백). "
                     "id 로 고르면 infra.combo, 직접 조합하면 infra.db/auth/hosting (섞어도 된다)")}


def pricing_problems(pricing):
    """infra.pricing 모양 — {checked: 'YYYY-MM-DD', combos: {id: {small, medium_plus, sources[]}}}."""
    if pricing is None:
        return []
    if not isinstance(pricing, dict):
        return ["infra.pricing 이 객체가 아니다"]
    out = []
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(pricing.get("checked") or "")):
        out.append("infra.pricing.checked 는 조회한 날짜 YYYY-MM-DD 다")
    combos = pricing.get("combos")
    if not isinstance(combos, dict) or not combos:
        out.append("infra.pricing.combos 가 비었다 ({조합 id: {small, medium_plus, sources[]}})")
        return out
    for cid, row in combos.items():
        if not combo(cid):
            out.append(f"infra.pricing.combos['{cid}'] 는 카탈로그에 없는 조합이다")
        elif not _fresh(pricing, cid):
            out.append(f"infra.pricing.combos['{cid}'] 에 small · medium_plus 문자열이 필요하다")
        elif not (isinstance(row.get("sources"), list) and row["sources"]):
            out.append(f"infra.pricing.combos['{cid}'].sources 에 읽은 요금 페이지 URL 을 적는다")
    return out


def expand(infra):
    """비어 있는 칸만 채운다 — 사람이 적은 값을 덮지 않는다."""
    if not isinstance(infra, dict):
        return infra
    out = dict(infra)
    if not out.get("scale"):
        s = scale_of(out.get("mau"), out.get("dau"))
        if s:
            out["scale"] = s
    c = combo(out.get("combo"))
    if c:
        for k in ("db", "auth", "hosting"):
            if not out.get(k):
                out[k] = c[k]
        sid = scale(out.get("scale"))
        if not out.get("cost") and sid:
            f = _fresh(out.get("pricing"), c["id"])
            if f:
                out["cost"] = f[sid["id"]]
                out["cost_checked"] = out["pricing"].get("checked")
                out["cost_sources"] = list(f.get("sources") or [SERVICES[n] for n in c["services"]])
            else:
                out["cost"] = c["cost"][sid["id"]]
                out["cost_basis"] = COST_BASIS   # 폴백으로 골랐다 — 사람이 요금표를 직접 봐야 한다
                out["cost_sources"] = [SERVICES[n] for n in c["services"]]
    return out
