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
from pathlib import Path


ROOT = Path(__file__).resolve().parent
COMPANIES_PATH = ROOT / "config" / "companies.json"
QUERY_PATTERNS_PATH = ROOT / "config" / "query_patterns.json"
ENV_PATH = ROOT / ".env.local"


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
]

STRONG_FUNCTIONS = {"Engineering", "Product", "Data/AI", "Design"}

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

EXCLUSION_TERMS = [
    "fractional head",
    "fractional",
    "freelance",
    "independent design engineer",
    "consultant",
    "agency",
    "advisor to founders",
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
    promotion_signal: str
    score: int
    confidence: str
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
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


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


def search_exa(query: str, company: str, company_tier: int, max_results: int = 5) -> list[SearchResult]:
    key = get_exa_key()
    if not key:
        return []
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
    results = []
    for item in payload.get("results", []):
        title = normalize_text(item.get("title", ""))
        url = item.get("url", "")
        snippet = normalize_text(item.get("text", "") or item.get("summary", ""))
        if not title or not url:
            continue
        results.append(SearchResult(title, url, snippet, query, company, company_tier, "Exa"))
    return results


def search_brave(query: str, company: str, company_tier: int, max_results: int = 5) -> list[SearchResult]:
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


def search_provider(query: str, company: str, company_tier: int, max_results: int = 5, provider: str = "auto") -> list[SearchResult]:
    if provider == "exa":
        return search_exa(query, company, company_tier, max_results)
    if provider == "brave":
        return search_brave(query, company, company_tier, max_results)
    if get_exa_key():
        return search_exa(query, company, company_tier, max_results)
    if get_brave_key():
        return search_brave(query, company, company_tier, max_results)
    raise RuntimeError("No search provider configured. Add EXA_API_KEY/EXA_API or BRAVE_SEARCH_API_KEY to .env.local.")


def infer_name(title: str) -> str:
    title = re.split(r"\s[-|]\s| LinkedIn", title, maxsplit=1)[0].strip()
    title = re.sub(r"\s+\|.*$", "", title).strip()
    return title[:120] or "Unknown"


def infer_function(text: str) -> str:
    lower = text.lower()
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


def has_exclusion_term(text: str) -> bool:
    header = text[:600].lower()
    return any(term in header for term in EXCLUSION_TERMS)


def has_startup_signal(text: str) -> bool:
    lower = text.lower()
    return any(term in lower for term in STARTUP_SIGNAL_TERMS)


def is_big_company_heavy_without_startup_signal(text: str) -> bool:
    lower = text.lower()
    big_company_hits = sum(1 for term in BIG_COMPANY_HEAVY_TERMS if term in lower)
    return big_company_hits >= 2 and not has_startup_signal(text)


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
    if has_exclusion_term(text):
        return None
    if has_weak_role(text) and function not in {"Engineering", "Product", "Data/AI"}:
        return None
    if is_big_company_heavy_without_startup_signal(text):
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
    if transition_signal_is_about_current_employer(text, current_company):
        return None
    current_months = current_role_tenure_months(text)
    if current_months is not None and current_months < 36 and not transition_signal:
        return None
    if not has_company_signal:
        return None
    if not (transition_signal or in_vesting_window):
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
    if has_startup_signal(text):
        score += 8

    score = max(0, min(score, 100))
    if score < 55:
        return None
    confidence = confidence_for_score(score)

    source = "LinkedIn" if "linkedin.com/in" in result.url else result.source
    title = infer_title(text)

    return Candidate(
        name=infer_name(result.title),
        linkedin_url=result.url if "linkedin.com/in" in result.url else "",
        current_company=current_company,
        current_title=title,
        function=function,
        signal_types=dedupe(signal_types),
        tenure_months=tenure_months,
        promotion_signal=promotion_signal,
        score=score,
        confidence=confidence,
        evidence=evidence[:1900],
        source=source,
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
        "Confidence": {"select": {"name": confidence_for_score(score)}},
        "Last Checked": {"date": {"start": dt.date.today().isoformat()}},
    }


def notion_candidate_properties(candidate: Candidate, *, include_status: bool) -> dict:
    properties = {
        "Name": {"title": [{"text": {"content": candidate.name}}]},
        "LinkedIn URL": {"url": candidate.linkedin_url or None},
        "Current Company": notion_text(candidate.current_company),
        "Current Title": notion_text(candidate.current_title),
        "Function": {"select": {"name": candidate.function}},
        "Signal Type": {"multi_select": [{"name": item} for item in candidate.signal_types]},
        "Promotion Signal": {"select": {"name": candidate.promotion_signal}},
        "Score": {"number": candidate.score},
        "Confidence": {"select": {"name": candidate.confidence}},
        "Evidence": notion_text(candidate.evidence),
        "Last Checked": {"date": {"start": dt.date.today().isoformat()}},
        "Source": {"select": {"name": candidate.source}},
    }
    if include_status:
        properties["Status"] = {"select": {"name": "New"}}
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


def write_candidate_to_notion(candidate: Candidate) -> str:
    database_id = os.environ["NOTION_DATABASE_ID"]
    existing_page_id = find_notion_page_by_linkedin_url(candidate.linkedin_url)
    properties = notion_candidate_properties(candidate, include_status=not bool(existing_page_id))
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
        old_confidence = notion_select_name(properties.get("Confidence", {}))
        new_confidence = confidence_for_score(new_score)
        name = notion_plain_text(properties.get("Name", {})) or page["id"]
        change = {
            "page_id": page["id"],
            "name": name,
            "status": score_input.status,
            "old_score": old_score,
            "new_score": new_score,
            "old_confidence": old_confidence,
            "new_confidence": new_confidence,
            "changed": old_score != new_score or old_confidence != new_confidence,
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


def discover(limit: int, per_company: int, provider: str, verbose: bool, excluded_urls: set[str] | None = None) -> list[Candidate]:
    companies = load_json(COMPANIES_PATH)
    patterns = load_json(QUERY_PATTERNS_PATH)
    candidates: list[Candidate] = []
    seen_urls = set()
    excluded_urls = excluded_urls or set()

    for company in companies:
        company_count = 0
        for pattern in patterns:
            query = pattern.format(company=company["name"])
            results = search_provider(query, company["name"], company["tier"], provider=provider)
            if verbose:
                print(f"{company['name']}: {len(results)} results for {query}", file=sys.stderr)
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
                if len(candidates) >= limit * 3 or company_count >= per_company:
                    break
            if len(candidates) >= limit * 3 or company_count >= per_company:
                break
            time.sleep(0.4)
        if len(candidates) >= limit * 3:
            break

    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates[:limit]


def main() -> int:
    load_env(ENV_PATH)
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--per-company", type=int, default=2)
    parser.add_argument("--provider", choices=["auto", "exa", "brave"], default="auto")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--write-notion", action="store_true")
    parser.add_argument("--output-json")
    parser.add_argument("--skip-existing-notion", action="store_true")
    parser.add_argument("--set-status", nargs=2, metavar=("LINKEDIN_URL", "STATUS"))
    parser.add_argument("--rescore-notion", action="store_true")
    parser.add_argument("--apply-rescore", action="store_true")
    args = parser.parse_args()

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

    excluded_urls: set[str] = set()
    if args.skip_existing_notion:
        missing = [key for key in ("NOTION_TOKEN", "NOTION_DATABASE_ID") if not os.environ.get(key)]
        if missing:
            raise RuntimeError(f"missing env values: {', '.join(missing)}")
        excluded_urls = load_existing_notion_linkedin_urls()
        if args.verbose:
            print(f"Loaded {len(excluded_urls)} existing Notion LinkedIn URLs to skip.", file=sys.stderr)

    candidates = discover(
        limit=args.limit,
        per_company=args.per_company,
        provider=args.provider,
        verbose=args.verbose,
        excluded_urls=excluded_urls,
    )
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
            action = write_candidate_to_notion(candidate)
            counts[action] += 1
        print(f"Notion sync complete: created={counts['created']} updated={counts['updated']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
