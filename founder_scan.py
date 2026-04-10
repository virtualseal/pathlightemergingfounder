#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


ROOT = Path(__file__).resolve().parent
COMPANIES_PATH = ROOT / "config" / "companies.json"
QUERY_PATTERNS_PATH = ROOT / "config" / "query_patterns.json"
ENV_PATH = ROOT / ".env.local"
EXA_CACHE_DIR = ROOT / "data" / "exa-cache"
SLACK_SENT_PATH = ROOT / "data" / "slack-candidates.json"
EVIDENCE_CHAR_LIMIT = 600


FOUNDER_TERMS = [
    "founder",
    "co-founder",
    "cofounder",
    "founding",
    "stealth",
    "building",
    "started",
    "starting",
    "exploring",
]

TRANSITION_TERMS = [
    "building",
    "stealth",
    "something new",
    "exploring",
    "independent",
    "sabbatical",
    "advisor",
    "angel investor",
    "eir",
]

ROLE_TERMS = [
    "engineer",
    "engineering",
    "software",
    "product",
    "pm",
    "designer",
    "design",
    "data",
    "ml",
    "ai",
    "growth",
    "compliance",
    "partnership",
    "partnerships",
    "strategy",
    "operations",
]

STRONG_FUNCTIONS = {"Engineering", "Product", "Data/AI", "Design", "Growth/GTM", "Other"}

WEAK_ROLE_TERMS = [
    "account manager",
    "partnerships lead",
    "partner manager",
    "sales",
    "account executive",
    "customer success",
    "business development",
    "fractional",
    "freelance",
    "consultant",
    "consulting",
    "services",
]

STARTUP_SIGNAL_TERMS = [
    "startup",
    "founding",
    "early",
    "0 to 1",
    "zero to one",
    "seed",
    "pre-seed",
    "yc",
    "venture",
    "angel",
    "stealth",
    "building something new",
    "launched",
    "built",
    "first engineer",
    "early employee",
]

ENTREPRENEURIAL_SIGNAL_TERMS = [
    "zero to one",
    "0 to 1",
    "built from scratch",
    "launched",
    "side project",
    "angel investor",
    "advisor",
    "startup",
    "ventures",
    "entrepreneur",
    "entrepreneurial",
    "creator",
    "open source",
    "patent",
    "first engineer",
    "founding engineer",
    "new business",
    "new product",
    "new market",
]

EXCLUSION_TERMS = [
    "fractional head",
    "fractional",
    "freelance",
    "independent design engineer",
    "consultant",
    "agency",
    "advisor to founders",
    "gtm leader",
    "head of partnerships",
    "head of product partnerships",
    "product partnerships",
    "strategic partnerships",
    "partnerships lead",
    "eir",
    "executive in residence",
    "venture capital",
]

CURRENT_STARTUP_EMPLOYEE_TERMS = [
    "Company: 1-10 employees",
    "Company: 11-50 employees",
    "Privately Held",
]

BIG_COMPANY_HEAVY_TERMS = [
    "apple",
    "microsoft",
    "disney",
    "amazon",
    "google",
    "meta",
    "oracle",
    "ibm",
]

PROMOTION_TERMS = [
    "staff",
    "principal",
    "lead",
    "manager",
    "director",
    "head of",
    "vp",
    "senior",
]

US_MARKERS = [
    "United States (US)",
    "United States",
    "San Francisco",
    "Bay Area",
    "New York",
    "Seattle",
    "Los Angeles",
    "Boston",
    "Austin",
    "Miami",
    "Chicago",
    "Washington",
    "Denver",
    "California",
    "Massachusetts",
    "Texas",
    "Florida",
]

STEALTH_TERMS = ["stealth", "something new", "building"]

REJECTED_STATUS = "Rejected"
LOW_CONFIDENCE_MAX = 54
MEDIUM_CONFIDENCE_MAX = 74


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    query: str
    company: str
    company_tier: int
    source: str


@dataclass
class Candidate:
    name: str
    linkedin_url: str
    current_company: str
    current_title: str
    function: str
    signal_types: list[str]
    tenure_months: int | None
    score: int
    evidence: str
    source: str


@dataclass
class ScoreInput:
    function: str
    signal_types: list[str]
    tenure_months: int | None
    promotion_signal: str
    evidence_text: str
    company_tier: int = 0
    status: str | None = None


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)
        if value.startswith("xapp-"):
            os.environ.setdefault("SLACK_APP_TOKEN", value)
        elif value.startswith("xoxb-"):
            os.environ.setdefault("SLACK_BOT_TOKEN", value)
        elif "channel" in key.lower() and re.match(r"^[CG][A-Z0-9]+$", value):
            os.environ.setdefault("SLACK_CHANNEL_ID", value)


def load_json(path: Path):
    return json.loads(path.read_text())


def fetch_url(url: str, *, method: str = "GET", body: dict | None = None, headers: dict | None = None) -> tuple[int, str]:
    data = None
    request_headers = headers or {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request_headers = {"Content-Type": "application/json", **request_headers}
    request = urllib.request.Request(url, data=data, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def get_exa_key() -> str | None:
    return os.environ.get("EXA_API_KEY") or os.environ.get("EXA_API")


def get_brave_key() -> str | None:
    return os.environ.get("BRAVE_SEARCH_API_KEY") or os.environ.get("BRAVE_API_KEY") or os.environ.get("BRAVE_API")


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def cache_path_for_query(provider: str, query: str) -> Path:
    digest = sha256(f"{provider}:{query}".encode("utf-8")).hexdigest()
    return EXA_CACHE_DIR / f"{digest}.json"


def search_exa(query: str, company: str, company_tier: int, max_results: int = 3, use_cache: bool = True) -> list[SearchResult]:
    key = get_exa_key()
    if not key:
        return []
    cache_path = cache_path_for_query("exa", query)
    if use_cache and cache_path.exists():
        payload = json.loads(cache_path.read_text())
        return search_results_from_exa_payload(payload, query, company, company_tier)

    body = {
        "query": query,
        "numResults": max_results,
        "type": "auto",
        "contents": {
            "text": {"maxCharacters": 1200}
        },
    }
    headers = {
        "x-api-key": key,
        "User-Agent": "founder-signal-research/1.0",
    }
    status, text = fetch_url("https://api.exa.ai/search", method="POST", body=body, headers=headers)
    if status not in (200, 201):
        print(f"exa search failed status={status} query={query}: {text[:300]}", file=sys.stderr)
        return []
    payload = json.loads(text)
    if use_cache:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, indent=2))
    return search_results_from_exa_payload(payload, query, company, company_tier)


def search_results_from_exa_payload(payload: dict, query: str, company: str, company_tier: int) -> list[SearchResult]:
    results = []
    for item in payload.get("results", []):
        title = normalize_text(item.get("title", ""))
        url = item.get("url", "")
        snippet = normalize_text(item.get("text", "") or item.get("summary", ""))
        if not title or not url:
            continue
        results.append(SearchResult(title, url, snippet, query, company, company_tier, "Exa"))
    return results


def search_brave(query: str, company: str, company_tier: int, max_results: int = 3) -> list[SearchResult]:
    key = get_brave_key()
    if not key:
        return []
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({
        "q": query,
        "count": max_results,
        "search_lang": "en",
        "country": "us",
        "safesearch": "moderate",
    })
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": key,
        "User-Agent": "founder-signal-research/1.0",
    }
    status, text = fetch_url(url, headers=headers)
    if status != 200:
        print(f"brave search failed status={status} query={query}: {text[:300]}", file=sys.stderr)
        return []
    payload = json.loads(text)
    results = []
    for item in payload.get("web", {}).get("results", []):
        title = normalize_text(item.get("title", ""))
        url = item.get("url", "")
        snippet = normalize_text(item.get("description", ""))
        if not title or not url:
            continue
        results.append(SearchResult(title, url, snippet, query, company, company_tier, "Brave Search"))
    return results


def search_provider(query: str, company: str, company_tier: int, max_results: int = 3, provider: str = "auto", use_cache: bool = True) -> list[SearchResult]:
    if provider == "exa":
        return search_exa(query, company, company_tier, max_results, use_cache=use_cache)
    if provider == "brave":
        return search_brave(query, company, company_tier, max_results)
    if get_exa_key():
        return search_exa(query, company, company_tier, max_results, use_cache=use_cache)
    if get_brave_key():
        return search_brave(query, company, company_tier, max_results)
    raise RuntimeError("No search provider configured. Add EXA_API_KEY/EXA_API or BRAVE_SEARCH_API_KEY to .env.local.")


def infer_name(title: str) -> str:
    title = re.split(r"\s[-|]\s| LinkedIn", title, maxsplit=1)[0].strip()
    title = re.sub(r"\s+\|.*$", "", title).strip()
    return title[:120] or "Unknown"


def infer_function(text: str) -> str:
    lower = text.lower()
    if any(term in lower for term in ["compliance", "product compliance", "legal"]):
        return "Product"
    if any(term in lower for term in ["product", " pm ", "product manager"]):
        return "Product"
    if any(term in lower for term in ["engineer", "engineering", "software", "developer"]):
        return "Engineering"
    if any(term in lower for term in ["data", "machine learning", " ml ", " ai "]):
        return "Data/AI"
    if "design" in lower or "designer" in lower:
        return "Design"
    if "growth" in lower or "gtm" in lower:
        return "Growth/GTM"
    return "Other"


def infer_title(text: str) -> str:
    title_patterns = [
        r"(founder[^,.|;-]*)",
        r"(co-founder[^,.|;-]*)",
        r"(staff engineer[^,.|;-]*)",
        r"(senior product manager[^,.|;-]*)",
        r"(product lead[^,.|;-]*)",
        r"(engineering manager[^,.|;-]*)",
        r"(software engineer[^,.|;-]*)",
    ]
    lower = text.lower()
    for pattern in title_patterns:
        match = re.search(pattern, lower)
        if match:
            return match.group(1).strip().title()
    return ""


def infer_current_company(text: str) -> str:
    match = re.search(r"\b(?:Founder|Co Founder|Co-Founder|CEO|CTO|CPO|Head Of Engineering|Engineering Manager|Product Lead|Product Manager|Staff Engineer)\s+(?:at|@)\s+([^,\n|#]+)", text, re.IGNORECASE)
    if match:
        return normalize_text(match.group(1))[:120]
    match = re.search(r"###\s+([^#\n]+?)\s+(?:\(Current\)|Current)", text, re.IGNORECASE)
    if match:
        title_line = normalize_text(match.group(1))
        at_match = re.search(r"\s+(?:at|@)\s+(.+)$", title_line, re.IGNORECASE)
        if at_match:
            return at_match.group(1)[:120]
    return ""


def parse_duration_months(value: str) -> int | None:
    years_match = re.search(r"(\d+)\s+years?", value, re.IGNORECASE)
    months_match = re.search(r"(\d+)\s+months?", value, re.IGNORECASE)
    if not years_match and not months_match:
        return None
    years = int(years_match.group(1)) if years_match else 0
    months = int(months_match.group(1)) if months_match else 0
    return years * 12 + months


def infer_tenure_months(text: str, company: str) -> int | None:
    escaped = re.escape(company)
    patterns = [
        rf"###\s+[^#\n]*(?:at|@)\s+{escaped}[^#]*?(?:Present|\d{{4}})\s+•\s+([^#\n]+)",
        rf"(?:at|@)\s+{escaped}[^#]*?\s+•\s+([^#\n]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        duration = parse_duration_months(match.group(1))
        if duration is not None:
            return duration
    return None


def infer_current_founder_months(text: str) -> int | None:
    match = re.search(
        r"###\s+[^#\n]*(?:Founder|Co-Founder|Co Founder|CEO|CTO)[^#\n]*\(Current\).*?Present\s+•\s+([^#\n]+)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    return parse_duration_months(match.group(1))


def has_us_location(text: str, url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    if host and host != "www.linkedin.com":
        return False
    header_text = text[:450]
    return any(marker.lower() in header_text.lower() for marker in US_MARKERS)


def has_weak_role(text: str) -> bool:
    lower = text.lower()
    return any(term in lower for term in WEAK_ROLE_TERMS)


def has_exclusion_term(text: str, *, transition_signal: bool = False) -> bool:
    header = text[:600].lower()
    if transition_signal:
        allowed_with_transition = [
            "partnerships lead",
            "strategic partnerships",
            "head of partnerships",
            "head of product partnerships",
            "product partnerships",
            "business development",
        ]
        reduced_terms = [term for term in EXCLUSION_TERMS if term not in allowed_with_transition]
        return any(term in header for term in reduced_terms)
    return any(term in header for term in EXCLUSION_TERMS)


def has_startup_signal(text: str) -> bool:
    lower = text.lower()
    return any(term in lower for term in STARTUP_SIGNAL_TERMS)


def has_entrepreneurial_signal(text: str) -> bool:
    lower = text.lower()
    return any(term in lower for term in ENTREPRENEURIAL_SIGNAL_TERMS)


def has_big_company_terms(text: str) -> bool:
    lower = text.lower()
    source_hits = sum(1 for term in BIG_COMPANY_HEAVY_TERMS if term in lower)
    public_company_or_large = "public company" in lower or "10,001+ employees" in lower or "5001-10,000 employees" in lower
    return source_hits >= 1 or public_company_or_large


def is_big_company_heavy_without_startup_signal(text: str) -> bool:
    lower = text.lower()
    big_company_hits = sum(1 for term in BIG_COMPANY_HEAVY_TERMS if term in lower)
    return big_company_hits >= 2 and not (has_startup_signal(text) or has_entrepreneurial_signal(text))


def has_transition_signal(text: str) -> bool:
    header = text[:260].lower()
    headline_patterns = [
        r"\bbuilding\b",
        r"\bstealth\b",
        r"\bsomething new\b",
        r"\bexploring\b",
        r"\bindependent\b",
        r"\bsabbatical\b",
        r"\bangel investor\b",
        r"\beir\b",
    ]
    return any(re.search(pattern, header) for pattern in headline_patterns)


def current_role_tenure_months(text: str) -> int | None:
    match = re.search(r"###\s+[^#\n]+\(Current\).*?Present\s+•\s+([^#\n]+)", text, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return parse_duration_months(match.group(1))


def current_role_is_small_startup_employee(text: str) -> bool:
    current_block_match = re.search(r"###\s+[^#\n]+\(Current\).*?(?=###|$)", text, re.IGNORECASE | re.DOTALL)
    if not current_block_match:
        return False
    block = current_block_match.group(0)
    if re.search(r"(Founder|Co-Founder|Co Founder)", block, re.IGNORECASE):
        return False
    return "Privately Held" in block and any(term in block for term in CURRENT_STARTUP_EMPLOYEE_TERMS[:2])


def transition_signal_is_about_current_employer(text: str, current_company: str) -> bool:
    if not current_company:
        return False
    header = text[:260].lower()
    company = current_company.lower()
    return "building" in header and company in header and not any(term in header for term in ["something new", "stealth", "ex-"])


def is_named_current_founder(text: str, current_company: str) -> bool:
    founder_current = re.search(r"###\s+[^#\n]*(?:Founder|Co-Founder|Co Founder|CEO|CTO)[^#\n]*\(Current\)", text, re.IGNORECASE)
    if not founder_current:
        return False
    company = current_company.lower()
    is_stealth = any(term in company or term in text.lower() for term in STEALTH_TERMS)
    is_generic_startup = "startup" in company and "stealth" not in company
    if is_generic_startup:
        months = infer_current_founder_months(text)
        early_unnamed = months is not None and months <= 12 and has_transition_signal(text)
        return not early_unnamed
    return not is_stealth


def confidence_for_score(score: int) -> str:
    if score <= LOW_CONFIDENCE_MAX:
        return "Low"
    if score <= MEDIUM_CONFIDENCE_MAX:
        return "Medium"
    return "High"


def founder_score(score_input: ScoreInput) -> int:
    status = (score_input.status or "").strip().lower()
    if status == REJECTED_STATUS.lower():
        return 0

    signal_types = set(score_input.signal_types)
    evidence = score_input.evidence_text.lower()
    score = 25

    function_weights = {
        "Engineering": 16,
        "Product": 14,
        "Data/AI": 14,
        "Design": 8,
        "Growth/GTM": 3,
        "Other": -20,
    }
    score += function_weights.get(score_input.function, -10)
    score += min(max(score_input.company_tier, 0), 3) * 5

    if "Top Source Company" in signal_types:
        score += 12
    if "Founder Language" in signal_types:
        score += 18
    if "Recent Departure" in signal_types:
        score += 12
    if "Vesting Window" in signal_types:
        score += 14
    if "Fast Promotions" in signal_types:
        score += 8

    if score_input.promotion_signal == "High":
        score += 8
    elif score_input.promotion_signal == "Medium":
        score += 4
    elif score_input.promotion_signal == "Low":
        score -= 4

    tenure = score_input.tenure_months
    if tenure is None:
        score -= 4
    elif 39 <= tenure <= 60:
        score += 16
    elif 24 <= tenure < 39:
        score += 6
    elif 60 < tenure <= 84:
        score += 4
    elif tenure < 12:
        score -= 14
    elif tenure > 120:
        score -= 20
    elif tenure > 84:
        score -= 10

    if has_startup_signal(evidence):
        score += 8
    if has_weak_role(evidence):
        score -= 25
    if has_exclusion_term(evidence):
        score -= 30
    if is_big_company_heavy_without_startup_signal(evidence):
        score -= 20

    return max(0, min(score, 100))


def score_result(result: SearchResult) -> Candidate | None:
    text = f"{result.title} {result.snippet}"
    lower = text.lower()
    if "linkedin.com/in" not in result.url:
        return None
    if not has_us_location(text, result.url):
        return None

    function = infer_function(text)
    if function not in STRONG_FUNCTIONS:
        return None

    current_company = infer_current_company(text)
    if is_named_current_founder(text, current_company):
        return None
    if current_role_is_small_startup_employee(text):
        return None

    has_company_signal = result.company.lower() in lower
    tenure_months = infer_tenure_months(text, result.company)
    in_vesting_window = bool(tenure_months and 39 <= tenure_months <= 60)
    transition_signal = has_transition_signal(text)
    startup_signal = has_startup_signal(text)
    entrepreneurial_signal = has_entrepreneurial_signal(text)
    big_company_profile = has_big_company_terms(text)
    if has_exclusion_term(text, transition_signal=transition_signal):
        return None
    if has_weak_role(text) and function not in {"Engineering", "Product", "Data/AI"} and not transition_signal:
        return None
    if is_big_company_heavy_without_startup_signal(text) and not transition_signal:
        return None
    if transition_signal_is_about_current_employer(text, current_company):
        return None
    current_months = current_role_tenure_months(text)
    if current_months is not None and current_months < 36 and not transition_signal:
        return None
    if not has_company_signal:
        return None
    if not (transition_signal or startup_signal or entrepreneurial_signal or in_vesting_window):
        return None
    if in_vesting_window and big_company_profile and not (transition_signal or startup_signal or entrepreneurial_signal):
        return None
    if big_company_profile and not (transition_signal or startup_signal or entrepreneurial_signal):
        return None

    signal_types = ["Top Source Company"]
    if transition_signal:
        signal_types.append("Founder Language")
    promotion_hits = sum(1 for term in PROMOTION_TERMS if term in lower)
    if promotion_hits >= 2:
        promotion_signal = "High"
        signal_types.append("Fast Promotions")
    elif promotion_hits == 1:
        promotion_signal = "Medium"
    else:
        promotion_signal = "Unknown"
    if any(term in lower for term in ["left", "former", "ex-"]):
        signal_types.append("Recent Departure")
    if in_vesting_window:
        signal_types.append("Vesting Window")
    elif tenure_months and tenure_months < 39 and not transition_signal:
        return None

    evidence = f"{result.snippet} Source query: {result.query}. Evidence URL: {result.url}"
    score_text = f"{text} {evidence}"
    score = founder_score(
        ScoreInput(
            function=function,
            signal_types=signal_types,
            tenure_months=tenure_months,
            promotion_signal=promotion_signal,
            evidence_text=score_text,
            company_tier=result.company_tier,
        )
    )

    current_founder_months = infer_current_founder_months(text)
    if current_founder_months:
        if current_founder_months <= 6:
            score += 6
        elif current_founder_months <= 12:
            score -= 5
        elif current_founder_months <= 24:
            score -= 25
        else:
            score -= 40

    if has_weak_role(text):
        score -= 25
    if startup_signal:
        score += 8
    if big_company_profile and not transition_signal:
        score -= 25

    score = max(0, min(score, 100))
    if score < 55:
        return None
    title = infer_title(text)

    return Candidate(
        name=infer_name(result.title),
        linkedin_url=result.url if "linkedin.com/in" in result.url else "",
        current_company=current_company,
        current_title=title,
        function=function,
        signal_types=dedupe(signal_types),
        tenure_months=tenure_months,
        score=score,
        evidence=evidence[:EVIDENCE_CHAR_LIMIT],
        source=result.source,
    )


def dedupe(values: list[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        if value not in seen:
            seen.add(value)
            output.append(value)
    return output


def canonical_url(value: str) -> str:
    return value.strip().split("?")[0].rstrip("/")


def notion_headers() -> dict:
    token = os.environ["NOTION_TOKEN"]
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def retrieve_notion_database() -> dict:
    database_id = os.environ["NOTION_DATABASE_ID"]
    status, text = fetch_url(
        f"https://api.notion.com/v1/databases/{database_id}",
        headers=notion_headers(),
    )
    if status not in (200, 201):
        raise RuntimeError(f"Notion database fetch failed status={status}: {text}")
    return json.loads(text)


def remove_notion_property(property_name: str) -> bool:
    database_id = os.environ["NOTION_DATABASE_ID"]
    database = retrieve_notion_database()
    if property_name not in database.get("properties", {}):
        return False
    status, text = fetch_url(
        f"https://api.notion.com/v1/databases/{database_id}",
        method="PATCH",
        body={"properties": {property_name: None}},
        headers=notion_headers(),
    )
    if status not in (200, 201):
        raise RuntimeError(f"Notion property removal failed status={status}: {text}")
    return True


def rename_notion_property(old_name: str, new_name: str) -> bool:
    database_id = os.environ["NOTION_DATABASE_ID"]
    database = retrieve_notion_database()
    if old_name not in database.get("properties", {}):
        return False
    status, text = fetch_url(
        f"https://api.notion.com/v1/databases/{database_id}",
        method="PATCH",
        body={"properties": {old_name: {"name": new_name}}},
        headers=notion_headers(),
    )
    if status not in (200, 201):
        raise RuntimeError(f"Notion property rename failed status={status}: {text}")
    return True


def slack_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}",
        "Content-Type": "application/json",
    }


def notion_text(value: str) -> dict:
    return {"rich_text": [{"text": {"content": value[:2000]}}]} if value else {"rich_text": []}


def notion_plain_text(prop: dict) -> str:
    if prop.get("type") == "title":
        return normalize_text(" ".join(item.get("plain_text", "") for item in prop.get("title", [])))
    if prop.get("type") == "rich_text":
        return normalize_text(" ".join(item.get("plain_text", "") for item in prop.get("rich_text", [])))
    return ""


def notion_select_name(prop: dict) -> str:
    selected = prop.get("select")
    return selected.get("name", "") if selected else ""


def notion_multi_select_names(prop: dict) -> list[str]:
    return [item.get("name", "") for item in prop.get("multi_select", []) if item.get("name")]


def notion_number(prop: dict) -> int | None:
    value = prop.get("number")
    return int(value) if value is not None else None


def notion_page_score_input(page: dict) -> ScoreInput:
    properties = page.get("properties", {})
    evidence_parts = [
        notion_plain_text(properties.get("Name", {})),
        notion_plain_text(properties.get("Current Company", {})),
        notion_plain_text(properties.get("Current Title", {})),
        notion_plain_text(properties.get("Evidence", {})),
    ]
    return ScoreInput(
        function=notion_select_name(properties.get("Function", {})),
        signal_types=notion_multi_select_names(properties.get("Signal Type", {})),
        tenure_months=notion_number(properties.get("Tenure Months", {})),
        promotion_signal=notion_select_name(properties.get("Promotion Signal", {})),
        evidence_text=" ".join(part for part in evidence_parts if part),
        status=notion_select_name(properties.get("Status", {})),
    )


def notion_score_properties(score: int) -> dict:
    return {
        "Score": {"number": score},
        "Last Checked": {"date": {"start": dt.date.today().isoformat()}},
    }


def notion_candidate_properties(candidate: Candidate, *, include_status: bool, status_name: str = "New") -> dict:
    properties = {
        "Name": {"title": [{"text": {"content": candidate.name}}]},
        "LinkedIn URL": {"url": candidate.linkedin_url or None},
        "Current Company": notion_text(candidate.current_company),
        "Current Title": notion_text(candidate.current_title),
        "Function": {"select": {"name": candidate.function}},
        "Signal Type": {"multi_select": [{"name": item} for item in candidate.signal_types]},
        "Score": {"number": candidate.score},
        "Evidence": notion_text(candidate.evidence),
        "Last Checked": {"date": {"start": dt.date.today().isoformat()}},
        "API": {"select": {"name": candidate.source}},
    }
    if include_status:
        properties["Status"] = {"select": {"name": status_name}}
    if candidate.tenure_months is not None:
        properties["Tenure Months"] = {"number": candidate.tenure_months}
    return properties


def get_notion_page(page_id: str) -> dict:
    status, text = fetch_url(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=notion_headers(),
    )
    if status not in (200, 201):
        raise RuntimeError(f"Notion page read failed status={status}: {text}")
    return json.loads(text)


def find_notion_page_by_linkedin_url(linkedin_url: str) -> str | None:
    if not linkedin_url:
        return None
    linkedin_url = canonical_url(linkedin_url)
    database_id = os.environ["NOTION_DATABASE_ID"]
    body = {
        "filter": {
            "property": "LinkedIn URL",
            "url": {
                "equals": linkedin_url,
            },
        },
        "page_size": 1,
    }
    status, text = fetch_url(
        f"https://api.notion.com/v1/databases/{database_id}/query",
        method="POST",
        body=body,
        headers=notion_headers(),
    )
    if status not in (200, 201):
        raise RuntimeError(f"Notion query failed status={status}: {text}")
    payload = json.loads(text)
    results = payload.get("results", [])
    return results[0]["id"] if results else None


def query_notion_pages_by_status(status_name: str) -> list[dict]:
    database_id = os.environ["NOTION_DATABASE_ID"]
    pages: list[dict] = []
    body: dict = {
        "filter": {
            "property": "Status",
            "select": {
                "equals": status_name,
            },
        },
        "page_size": 100,
    }
    while True:
        status, text = fetch_url(
            f"https://api.notion.com/v1/databases/{database_id}/query",
            method="POST",
            body=body,
            headers=notion_headers(),
        )
        if status not in (200, 201):
            raise RuntimeError(f"Notion query failed status={status}: {text}")
        payload = json.loads(text)
        pages.extend(payload.get("results", []))
        if not payload.get("has_more"):
            return pages
        body["start_cursor"] = payload["next_cursor"]


def page_id_slug(page_id: str) -> str:
    return page_id.replace("-", "")


def plain_text(items: list[dict]) -> str:
    return "".join(item.get("plain_text", "") for item in items)


def prop_title(properties: dict, name: str) -> str:
    return plain_text(properties.get(name, {}).get("title", []))


def prop_rich_text(properties: dict, name: str) -> str:
    return plain_text(properties.get(name, {}).get("rich_text", []))


def prop_select(properties: dict, name: str) -> str:
    select = properties.get(name, {}).get("select")
    return select.get("name", "") if select else ""


def query_all_notion_pages() -> list[dict]:
    database_id = os.environ["NOTION_DATABASE_ID"]
    pages: list[dict] = []
    body: dict = {"page_size": 100}
    while True:
        status, text = fetch_url(
            f"https://api.notion.com/v1/databases/{database_id}/query",
            method="POST",
            body=body,
            headers=notion_headers(),
        )
        if status not in (200, 201):
            raise RuntimeError(f"Notion query failed status={status}: {text}")
        payload = json.loads(text)
        pages.extend(payload.get("results", []))
        if not payload.get("has_more"):
            return pages
        body["start_cursor"] = payload["next_cursor"]


def update_select_property_by_page_id(page_id: str, property_name: str, value: str) -> None:
    status, text = fetch_url(
        f"https://api.notion.com/v1/pages/{page_id}",
        method="PATCH",
        body={"properties": {property_name: {"select": {"name": value}}}},
        headers=notion_headers(),
    )
    if status not in (200, 201):
        raise RuntimeError(f"Notion select update failed status={status}: {text}")


def replace_select_property_value(property_name: str, old_value: str, new_value: str) -> int:
    updated = 0
    for page in query_all_notion_pages():
        if prop_select(page.get("properties", {}), property_name) != old_value:
            continue
        update_select_property_by_page_id(page["id"], property_name, new_value)
        updated += 1
    return updated


def prop_multi_select(properties: dict, name: str) -> list[str]:
    return [item.get("name", "") for item in properties.get(name, {}).get("multi_select", []) if item.get("name")]


def prop_number(properties: dict, name: str) -> int | float | None:
    return properties.get(name, {}).get("number")


def prop_url(properties: dict, name: str) -> str:
    return properties.get(name, {}).get("url") or ""


def notion_page_url(page: dict) -> str:
    return page.get("url") or f"https://www.notion.so/{page_id_slug(page['id'])}"


def slack_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clean_profile_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    value = re.sub(r"^Public evidence:\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b[\d,]+\s+connections?\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b[\d,]+\s+followers?\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*[•|]\s*", " ", value)
    return re.sub(r"\s+", " ", value).strip(" -|")


def clean_title(value: str, company: str = "") -> str:
    value = clean_profile_text(value)
    value = re.split(r"\b(?:New York|San Francisco|Bay Area|Los Angeles|Seattle|Boston|Austin|Miami|Chicago|United States)\b", value, maxsplit=1, flags=re.IGNORECASE)[0]
    value = re.split(r"\b(?:About|Experience|Education)\b", value, maxsplit=1, flags=re.IGNORECASE)[0]
    if company:
        value = re.sub(rf"\s+(?:at|@)\s+{re.escape(company)}.*$", "", value, flags=re.IGNORECASE)
    return value.strip(" -|") or "Unknown"


def sentence_case(value: str) -> str:
    value = value.strip()
    return value[:1].upper() + value[1:] if value else value


def extract_about_text(raw_evidence: str) -> str:
    match = re.search(r"##\s+About\s+(.*?)(?:Total Experience:|##\s+Experience|###|Source query:)", raw_evidence, re.IGNORECASE)
    if not match:
        return ""
    about = clean_profile_text(match.group(1))
    about = re.sub(r"^Based in [^,]+,\s*", "", about, flags=re.IGNORECASE)
    return about.strip()


def concise_about_text(about: str) -> str:
    about = about.rstrip(" .")
    if "Portico" in about and "publishers" in about and "subscription" in about and "ad" in about:
        return "working on Portico, a paid-revenue product for online publishers trying to move beyond subscriptions and ads"
    return about


def concise_previous_text(previous: str) -> str:
    previous = previous.rstrip(" .")
    if "Figma" in previous and "Community" in previous and "code splitting" in previous:
        return "previously worked on Figma's Community product and code-splitting/performance"
    return previous


def extract_experience_sections(raw_evidence: str) -> list[dict]:
    sections = []
    pattern = re.compile(
        r"###\s+(?P<title>.*?)\s+at\s+(?P<company>.*?)\s+"
        r"(?P<dates>(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}\s+-\s+.*?)(?=###|Source query:|$)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(raw_evidence):
        body = clean_profile_text(match.group(0))
        company = clean_profile_text(match.group("company"))
        title = clean_profile_text(match.group("title"))
        description = ""
        dept_split = re.split(r"\bDepartment:", body, maxsplit=1, flags=re.IGNORECASE)[0]
        company_split = re.split(r"\bCompany:", dept_split, maxsplit=1, flags=re.IGNORECASE)
        if len(company_split) > 1:
            description = clean_profile_text(company_split[1])
            service_split = re.split(r"\b(?:Research Services|Design Services|Software Development|Internet Publishing)\b", description, maxsplit=1, flags=re.IGNORECASE)
            description = clean_profile_text(service_split[1]) if len(service_split) > 1 else ""
        sections.append({"title": title, "company": company, "description": description})
    return sections


def extract_previous_experience_text(raw_evidence: str, current_company: str) -> str:
    for section in extract_experience_sections(raw_evidence):
        company = section["company"]
        if current_company and current_company.lower() in company.lower():
            continue
        description = section["description"]
        if description:
            return f"Previously at {company}, {description}"
        return f"Previously {section['title']} at {company}"
    return ""


def extract_external_urls(raw_evidence: str) -> list[str]:
    urls = re.findall(r"https?://[^\s)>]+", raw_evidence)
    output = []
    for url in urls:
        clean_url = url.rstrip(".,")
        if any(domain in clean_url for domain in ("linkedin.com", "notion.so")):
            continue
        if clean_url not in output:
            output.append(clean_url)
    return output


def candidate_reason(properties: dict) -> str:
    name = prop_title(properties, "Name")
    company = prop_rich_text(properties, "Current Company")
    title = clean_title(prop_rich_text(properties, "Current Title"), company)
    score = prop_number(properties, "Score")
    signals = prop_multi_select(properties, "Signal Type")
    raw_evidence = clean_profile_text(prop_rich_text(properties, "Evidence"))
    if raw_evidence and not raw_evidence.startswith("#") and "##" not in raw_evidence and "Source query:" not in raw_evidence:
        return raw_evidence[:EVIDENCE_CHAR_LIMIT].rsplit(" ", 1)[0].rstrip(" .,") + "."
    about = concise_about_text(extract_about_text(raw_evidence))
    previous = concise_previous_text(extract_previous_experience_text(raw_evidence, company))

    role = ""
    if title != "Unknown" and company:
        role = f"{title} at {company}"
    elif company:
        role = f"someone currently at {company}"

    concrete = []
    if about:
        concrete.append(f"{name or 'Their profile'} says they are {about.rstrip(' .')}")
    if previous:
        concrete.append(sentence_case(previous))
    if concrete:
        summary = ". ".join(concrete)
        if role:
            summary = f"{summary}. Current role: {role}"
        if len(summary) <= EVIDENCE_CHAR_LIMIT:
            return summary.rstrip(" .,") + "."
        summary = ". ".join(concrete)
        if len(summary) <= EVIDENCE_CHAR_LIMIT:
            return summary.rstrip(" .,") + "."
        return summary[:EVIDENCE_CHAR_LIMIT].rsplit(" ", 1)[0].rstrip(" .,") + "."

    opening = ""
    if role:
        opening = f"{role} looks worth a founder-oriented outreach"
    else:
        opening = "This person looks worth a founder-oriented outreach"

    reasons = []
    if "Top Source Company" in signals and company:
        reasons.append(f"{company} is a high-signal source company")
    if "Founder Language" in signals:
        reasons.append("the result includes founder/building language")
    if "Recent Departure" in signals:
        reasons.append("there is a timely transition signal")
    if "Vesting Window" in signals:
        reasons.append("their tenure may fit a founder-transition window")

    summary = f"{opening}. " + "; ".join(reasons)
    if score is not None:
        summary = f"{summary}. Score: {score}/100"
    if len(summary) < 80 and raw_evidence:
        summary = f"{summary}. Supporting text: {raw_evidence}"
    if len(summary) <= EVIDENCE_CHAR_LIMIT:
        return summary.rstrip(" .,") + "."

    shorter_reasons = reasons[:3]
    summary = f"{opening}. " + "; ".join(shorter_reasons)
    if len(summary) <= EVIDENCE_CHAR_LIMIT:
        return summary.rstrip(" .,") + "."
    shorter_reasons = reasons[:2]
    summary = f"{opening}. " + "; ".join(shorter_reasons)
    if len(summary) <= EVIDENCE_CHAR_LIMIT:
        return summary.rstrip(" .,") + "."
    return summary[:EVIDENCE_CHAR_LIMIT].rsplit(" ", 1)[0].rstrip(" .,") + "."


def load_slack_sent() -> dict:
    if not SLACK_SENT_PATH.exists():
        return {"pages": {}, "messages": {}}
    payload = json.loads(SLACK_SENT_PATH.read_text())
    payload.setdefault("pages", {})
    payload.setdefault("messages", {})
    return payload


def save_slack_sent(payload: dict) -> None:
    SLACK_SENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SLACK_SENT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True))


def slack_message_key(channel: str, ts: str) -> str:
    return f"{channel}:{ts}"


def slack_blocks_for_notion_page(page: dict) -> list[dict]:
    properties = page.get("properties", {})
    name = prop_title(properties, "Name") or "Unknown candidate"
    linkedin_url = prop_url(properties, "LinkedIn URL")
    current_company = prop_rich_text(properties, "Current Company")
    current_title = clean_title(prop_rich_text(properties, "Current Title"), current_company)
    score = prop_number(properties, "Score")
    reason = candidate_reason(properties)
    notion_url = notion_page_url(page)
    external_urls = extract_external_urls(prop_rich_text(properties, "Evidence"))

    lines = []
    for label, value in (
        ("Score", str(score) if score is not None else ""),
        ("Company", current_company),
        ("Title", current_title),
    ):
        if value:
            lines.append(f"*{label}:* {slack_escape(value)}")

    blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"New founder candidate: {name[:120]}"}}]
    if lines:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})
    if reason:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Why reach out:*\n{slack_escape(reason)}"}})
    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "React with :white_check_mark: to approve or :red_circle: to reject.",
            },
        }
    )
    profile_links = []
    if linkedin_url:
        profile_links.append(f"<{linkedin_url}|LinkedIn profile>")
    profile_links.extend(f"<{url}|Personal/external link>" for url in external_urls[:2])
    profile_links.append(f"<{notion_url}|Notion record>")
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Profile:*\n{chr(10).join(profile_links)}"}})
    return blocks


def post_notion_page_to_slack(page: dict) -> dict:
    channel_id = os.environ["SLACK_CHANNEL_ID"]
    name = prop_title(page.get("properties", {}), "Name") or "Unknown candidate"
    body = {
        "channel": channel_id,
        "text": f"New founder candidate: {name}. React with :white_check_mark: to approve or :red_circle: to reject.",
        "blocks": slack_blocks_for_notion_page(page),
        "unfurl_links": False,
        "unfurl_media": False,
    }
    status, text = fetch_url("https://slack.com/api/chat.postMessage", method="POST", body=body, headers=slack_headers())
    payload = json.loads(text)
    if status != 200 or not payload.get("ok"):
        raise RuntimeError(f"Slack post failed status={status}: {text}")
    return payload


def send_new_notion_pages_to_slack(*, resend: bool = False) -> tuple[int, int]:
    sent = load_slack_sent()
    pages = query_notion_pages_by_status("New")
    posted = 0
    skipped = 0
    for page in pages:
        page_id = page["id"]
        if not resend and page_id in sent["pages"]:
            skipped += 1
            continue
        response = post_notion_page_to_slack(page)
        channel = response["channel"]
        ts = response["ts"]
        record = {
            "page_id": page_id,
            "name": prop_title(page.get("properties", {}), "Name"),
            "linkedin_url": prop_url(page.get("properties", {}), "LinkedIn URL"),
            "notion_url": notion_page_url(page),
            "channel": channel,
            "ts": ts,
            "sent_at": dt.datetime.now(dt.UTC).isoformat(),
        }
        sent["pages"][page_id] = record
        sent["messages"][slack_message_key(channel, ts)] = record
        posted += 1
    save_slack_sent(sent)
    return posted, skipped


def load_existing_notion_linkedin_urls() -> set[str]:
    database_id = os.environ["NOTION_DATABASE_ID"]
    existing: set[str] = set()
    body: dict = {"page_size": 100}
    while True:
        status, text = fetch_url(
            f"https://api.notion.com/v1/databases/{database_id}/query",
            method="POST",
            body=body,
            headers=notion_headers(),
        )
        if status not in (200, 201):
            raise RuntimeError(f"Notion query failed status={status}: {text}")
        payload = json.loads(text)
        for page in payload.get("results", []):
            prop = page.get("properties", {}).get("LinkedIn URL", {})
            url = prop.get("url")
            if url:
                existing.add(canonical_url(url))
        if not payload.get("has_more"):
            return existing
        body["start_cursor"] = payload["next_cursor"]


def write_candidate_to_notion(candidate: Candidate, status_name: str = "New") -> str:
    database_id = os.environ["NOTION_DATABASE_ID"]
    existing_page_id = find_notion_page_by_linkedin_url(candidate.linkedin_url)
    properties = notion_candidate_properties(candidate, include_status=not bool(existing_page_id), status_name=status_name)
    if existing_page_id:
        existing_page = get_notion_page(existing_page_id)
        existing_status = notion_select_name(existing_page.get("properties", {}).get("Status", {}))
        if existing_status == REJECTED_STATUS:
            properties.update(notion_score_properties(0))
        status, text = fetch_url(
            f"https://api.notion.com/v1/pages/{existing_page_id}",
            method="PATCH",
            body={"properties": properties},
            headers=notion_headers(),
        )
        if status not in (200, 201):
            raise RuntimeError(f"Notion update failed status={status}: {text}")
        return "updated"

    body = {"parent": {"database_id": database_id}, "properties": properties}
    status, text = fetch_url("https://api.notion.com/v1/pages", method="POST", body=body, headers=notion_headers())
    if status not in (200, 201):
        raise RuntimeError(f"Notion write failed status={status}: {text}")
    return "created"


def update_status_by_linkedin_url(linkedin_url: str, status_name: str) -> bool:
    page_id = find_notion_page_by_linkedin_url(linkedin_url)
    if not page_id:
        page_id = find_notion_page_by_linkedin_url(canonical_url(linkedin_url) + "/")
    if not page_id:
        return False
    properties = {"Status": {"select": {"name": status_name}}}
    if status_name == REJECTED_STATUS:
        properties.update(notion_score_properties(0))
    status, text = fetch_url(
        f"https://api.notion.com/v1/pages/{page_id}",
        method="PATCH",
        body={"properties": properties},
        headers=notion_headers(),
    )
    if status not in (200, 201):
        raise RuntimeError(f"Notion status update failed status={status}: {text}")
    return True


def load_notion_pages() -> list[dict]:
    database_id = os.environ["NOTION_DATABASE_ID"]
    pages = []
    body: dict = {"page_size": 100}
    while True:
        status, text = fetch_url(
            f"https://api.notion.com/v1/databases/{database_id}/query",
            method="POST",
            body=body,
            headers=notion_headers(),
        )
        if status not in (200, 201):
            raise RuntimeError(f"Notion query failed status={status}: {text}")
        payload = json.loads(text)
        pages.extend(payload.get("results", []))
        if not payload.get("has_more"):
            return pages
        body["start_cursor"] = payload["next_cursor"]


def rescore_notion_database(*, apply: bool) -> list[dict]:
    changes = []
    for page in load_notion_pages():
        properties = page.get("properties", {})
        score_input = notion_page_score_input(page)
        new_score = founder_score(score_input)
        old_score = notion_number(properties.get("Score", {}))
        name = notion_plain_text(properties.get("Name", {})) or page["id"]
        change = {
            "page_id": page["id"],
            "name": name,
            "status": score_input.status,
            "old_score": old_score,
            "new_score": new_score,
            "changed": old_score != new_score,
        }
        changes.append(change)
        if apply and change["changed"]:
            status, text = fetch_url(
                f"https://api.notion.com/v1/pages/{page['id']}",
                method="PATCH",
                body={"properties": notion_score_properties(new_score)},
                headers=notion_headers(),
            )
            if status not in (200, 201):
                raise RuntimeError(f"Notion rescore update failed status={status}: {text}")
    return changes


def update_status_by_page_id(page_id: str, status_name: str) -> bool:
    status, text = fetch_url(
        f"https://api.notion.com/v1/pages/{page_id}",
        method="PATCH",
        body={"properties": {"Status": {"select": {"name": status_name}}}},
        headers=notion_headers(),
    )
    if status == 404:
        return False
    if status not in (200, 201):
        raise RuntimeError(f"Notion status update failed status={status}: {text}")
    return True


def candidate_from_row(row: dict) -> Candidate:
    row = dict(row)
    row.pop("confidence", None)
    row.pop("promotion_signal", None)
    return Candidate(**row)


def selected_patterns(pattern_config: dict | list, mode: str, company_tier: int) -> list[tuple[str, str]]:
    if isinstance(pattern_config, list):
        return [("legacy", pattern) for pattern in pattern_config]
    patterns: list[tuple[str, str]] = []
    if mode in ("all", "transition"):
        patterns.extend(("transition", pattern) for pattern in pattern_config.get("transition", []))
    if mode in ("all", "entrepreneurial") and company_tier >= 2:
        patterns.extend(("entrepreneurial", pattern) for pattern in pattern_config.get("entrepreneurial", []))
    if mode in ("all", "vesting") and company_tier >= 3:
        patterns.extend(("vesting", pattern) for pattern in pattern_config.get("vesting", []))
    return patterns


def discover(
    limit: int,
    per_company: int,
    provider: str,
    verbose: bool,
    excluded_urls: set[str] | None = None,
    query_mode: str = "all",
    max_queries: int = 40,
    max_queries_per_company: int = 4,
    stop_after_candidates: int | None = None,
    use_cache: bool = True,
    results_per_query: int = 3,
) -> list[Candidate]:
    companies = load_json(COMPANIES_PATH)
    pattern_config = load_json(QUERY_PATTERNS_PATH)
    candidates: list[Candidate] = []
    seen_urls = set()
    excluded_urls = excluded_urls or set()
    queries_used = 0
    target_pool_size = stop_after_candidates or limit * 2

    for company in companies:
        company_count = 0
        company_queries = 0
        for pattern_type, pattern in selected_patterns(pattern_config, query_mode, company["tier"]):
            if queries_used >= max_queries:
                if verbose:
                    print(f"Query budget reached: {queries_used}/{max_queries}", file=sys.stderr)
                break
            if company_queries >= max_queries_per_company:
                if verbose:
                    print(f"{company['name']}: company query cap reached {company_queries}/{max_queries_per_company}", file=sys.stderr)
                break
            query = pattern.format(company=company["name"])
            queries_used += 1
            company_queries += 1
            results = search_provider(query, company["name"], company["tier"], max_results=results_per_query, provider=provider, use_cache=use_cache)
            if verbose:
                print(f"{company['name']} [{pattern_type}] query {queries_used}/{max_queries}: {len(results)} results for {query}", file=sys.stderr)
            for result in results:
                result_url = canonical_url(result.url)
                if result_url in seen_urls or result_url in excluded_urls:
                    continue
                candidate = score_result(result)
                if not candidate:
                    continue
                seen_urls.add(result_url)
                candidates.append(candidate)
                company_count += 1
                if verbose:
                    print(f"  + {candidate.name} score={candidate.score}", file=sys.stderr)
                if len(candidates) >= target_pool_size or company_count >= per_company:
                    break
            if len(candidates) >= target_pool_size or company_count >= per_company:
                break
            time.sleep(0.4)
        if len(candidates) >= target_pool_size or queries_used >= max_queries:
            break

    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates[:limit]


def main() -> int:
    load_env(ENV_PATH)
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--per-company", type=int, default=2)
    parser.add_argument("--provider", choices=["auto", "exa", "brave"], default="auto")
    parser.add_argument("--query-mode", choices=["all", "transition", "entrepreneurial", "vesting"], default="all")
    parser.add_argument("--max-queries", type=int, default=40)
    parser.add_argument("--max-queries-per-company", type=int, default=4)
    parser.add_argument("--stop-after-candidates", type=int)
    parser.add_argument("--results-per-query", type=int, default=3)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--write-notion", action="store_true")
    parser.add_argument("--default-status", default="New")
    parser.add_argument("--output-json")
    parser.add_argument("--input-json")
    parser.add_argument("--only-url", action="append", default=[])
    parser.add_argument("--skip-existing-notion", action="store_true")
    parser.add_argument("--set-status", nargs=2, metavar=("LINKEDIN_URL", "STATUS"))
    parser.add_argument("--set-page-status", nargs=2, metavar=("PAGE_ID", "STATUS"))
    parser.add_argument("--remove-notion-property")
    parser.add_argument("--rename-notion-property", nargs=2, metavar=("OLD_NAME", "NEW_NAME"))
    parser.add_argument("--replace-select-value", nargs=3, metavar=("PROPERTY", "OLD_VALUE", "NEW_VALUE"))
    parser.add_argument("--send-new-slack", action="store_true")
    parser.add_argument("--resend-slack", action="store_true")
    parser.add_argument("--rescore-notion", action="store_true")
    parser.add_argument("--apply-rescore", action="store_true")
    args = parser.parse_args()

    if args.remove_notion_property:
        missing = [key for key in ("NOTION_TOKEN", "NOTION_DATABASE_ID") if not os.environ.get(key)]
        if missing:
            raise RuntimeError(f"missing env values: {', '.join(missing)}")
        removed = remove_notion_property(args.remove_notion_property)
        print(f"{'removed' if removed else 'not found'}: {args.remove_notion_property}")
        return 0

    if args.rename_notion_property:
        missing = [key for key in ("NOTION_TOKEN", "NOTION_DATABASE_ID") if not os.environ.get(key)]
        if missing:
            raise RuntimeError(f"missing env values: {', '.join(missing)}")
        old_name, new_name = args.rename_notion_property
        renamed = rename_notion_property(old_name, new_name)
        print(f"{'renamed' if renamed else 'not found'}: {old_name} -> {new_name}")
        return 0

    if args.replace_select_value:
        missing = [key for key in ("NOTION_TOKEN", "NOTION_DATABASE_ID") if not os.environ.get(key)]
        if missing:
            raise RuntimeError(f"missing env values: {', '.join(missing)}")
        property_name, old_value, new_value = args.replace_select_value
        updated = replace_select_property_value(property_name, old_value, new_value)
        print(f"updated {updated}: {property_name} {old_value} -> {new_value}")
        return 0

    if args.rescore_notion:
        missing = [key for key in ("NOTION_TOKEN", "NOTION_DATABASE_ID") if not os.environ.get(key)]
        if missing:
            raise RuntimeError(f"missing env values: {', '.join(missing)}")
        changes = rescore_notion_database(apply=args.apply_rescore)
        changed_count = sum(1 for change in changes if change["changed"])
        rejected_zeroed = sum(
            1
            for change in changes
            if change["status"] == REJECTED_STATUS and change["new_score"] == 0 and change["old_score"] != 0
        )
        print(json.dumps(changes, indent=2))
        mode = "applied" if args.apply_rescore else "dry run"
        print(f"Notion rescore {mode}: rows={len(changes)} changed={changed_count} rejected_zeroed={rejected_zeroed}.")
        return 0

    if args.set_status:
        missing = [key for key in ("NOTION_TOKEN", "NOTION_DATABASE_ID") if not os.environ.get(key)]
        if missing:
            raise RuntimeError(f"missing env values: {', '.join(missing)}")
        linkedin_url, status_name = args.set_status
        updated = update_status_by_linkedin_url(linkedin_url, status_name)
        print(f"{'updated' if updated else 'not found'}: {linkedin_url} -> {status_name}")
        return 0

    if args.set_page_status:
        missing = [key for key in ("NOTION_TOKEN", "NOTION_DATABASE_ID") if not os.environ.get(key)]
        if missing:
            raise RuntimeError(f"missing env values: {', '.join(missing)}")
        page_id, status_name = args.set_page_status
        updated = update_status_by_page_id(page_id, status_name)
        print(f"{'updated' if updated else 'not found'}: {page_id} -> {status_name}")
        return 0

    if args.send_new_slack:
        missing = [key for key in ("NOTION_TOKEN", "NOTION_DATABASE_ID", "SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID") if not os.environ.get(key)]
        if missing:
            raise RuntimeError(f"missing env values: {', '.join(missing)}")
        posted, skipped = send_new_notion_pages_to_slack(resend=args.resend_slack)
        print(f"Slack sync complete: posted={posted} skipped={skipped}.")
        return 0

    excluded_urls: set[str] = set()
    if args.skip_existing_notion:
        missing = [key for key in ("NOTION_TOKEN", "NOTION_DATABASE_ID") if not os.environ.get(key)]
        if missing:
            raise RuntimeError(f"missing env values: {', '.join(missing)}")
        excluded_urls = load_existing_notion_linkedin_urls()
        if args.verbose:
            print(f"Loaded {len(excluded_urls)} existing Notion LinkedIn URLs to skip.", file=sys.stderr)

    if args.input_json:
        rows = json.loads(Path(args.input_json).read_text())
        candidates = [candidate_from_row(row) for row in rows]
    else:
        candidates = discover(
            limit=args.limit,
            per_company=args.per_company,
            provider=args.provider,
            verbose=args.verbose,
            excluded_urls=excluded_urls,
            query_mode=args.query_mode,
            max_queries=args.max_queries,
            max_queries_per_company=args.max_queries_per_company,
            stop_after_candidates=args.stop_after_candidates,
            use_cache=not args.no_cache,
            results_per_query=args.results_per_query,
        )
    if args.only_url:
        allowed = {canonical_url(url) for url in args.only_url}
        candidates = [candidate for candidate in candidates if canonical_url(candidate.linkedin_url) in allowed]
    candidate_rows = [candidate.__dict__ for candidate in candidates]
    print(json.dumps(candidate_rows, indent=2))

    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(candidate_rows, indent=2))
        print(f"Wrote candidate JSON to {output_path}.")

    if args.write_notion:
        missing = [key for key in ("NOTION_TOKEN", "NOTION_DATABASE_ID") if not os.environ.get(key)]
        if missing:
            raise RuntimeError(f"missing env values: {', '.join(missing)}")
        counts = {"created": 0, "updated": 0}
        for candidate in candidates:
            action = write_candidate_to_notion(candidate, status_name=args.default_status)
            counts[action] += 1
        print(f"Notion sync complete: created={counts['created']} updated={counts['updated']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
