"""
daily_lead_hunter.py — Scan2Core Lead Hunter (GitHub Actions daily scraper)

Collects WA construction leads from sources that actually return data to a plain
HTTP client, scores them for GPR / core-drilling relevance, and attaches the
REAL contact for each lead — extracted from the lead's own page, never recalled
from memory. Outputs found_projects.json.

SOURCES (all verified live):
  1. City bid pages on Granicus/CivicEngage  → open solicitations (Bids.aspx?bidID=)
  2. Seattle permit open-data API (Socrata)   → active construction with addresses
GC national-portfolio pages and JavaScript portals (Seattle/King County/Bellevue/
WSDOT/Sound Transit/WEBS) are intentionally excluded: a urllib scraper gets an
empty shell from JS apps. Reaching those needs a headless browser (Playwright) —
a separate, heavier change.

CONTACTS ARE REAL, NOT GUESSED:
  • For city bids we deterministically harvest the page's structured
    "Contact Person:" block (e.g. "Lori Erickson, Public Works Analyst,
    lerickson@city.gov") — the actual named bid contact, no AI, no hallucination.
  • If a key is set, grounded AI extraction is a fallback that may ONLY use text
    present on the page.
  • A page with no contact yields a blank lead. We never attach a building's
    main switchboard number to a job.
"""

import json, os, re, time, datetime, hashlib, ssl, gzip
import urllib.request, urllib.error
from urllib.parse import urljoin, urlencode
from dataclasses import dataclass, field, asdict
from typing import Optional

# ── CONFIG ────────────────────────────────────────────────────────────────────
AI_MODEL          = "claude-haiku-4-5-20251001"
AI_SLEEP_SECONDS  = 2
BACKFILL_PER_RUN  = 40
OUTPUT_FILE       = "found_projects.json"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
PAGE_TEXT_LIMIT   = 6000
SHARED_PHONE_THRESHOLD = 5

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()


# ── DATA MODEL ────────────────────────────────────────────────────────────────
@dataclass
class ContactInfo:
    name:       Optional[str] = None
    title:      Optional[str] = None
    department: Optional[str] = None
    email:      Optional[str] = None
    phone:      Optional[str] = None

    def has_contact_info(self) -> bool:
        return bool(self.email or self.phone or self.name)


@dataclass
class Lead:
    name:               str
    source:             str
    url:                str
    city:               str  = ""
    county:             str  = ""
    state:              str  = "WA"
    priority:           int  = 6
    type:               str  = "Public Bid"
    bid_number:         str  = ""
    close_date:         str  = ""
    description:        str  = ""
    direct_url:         str  = ""
    found_date:         str  = field(default_factory=_utcnow_iso)
    contact_name:       str  = ""
    contact_title:      str  = ""
    contact_department: str  = ""
    contact_email:      str  = ""
    contact_phone:      str  = ""
    enriched:           Optional[str] = None   # None | 'ai' | 'ai_partial' | 'none'

    def is_gc(self) -> bool:
        return re.match(r'^GC[\s\-–—]', self.source or '') is not None

    def is_noise_lead(self) -> bool:
        return is_noise(self.name)

    def lead_id(self) -> str:
        return hashlib.md5(f"{self.name}|{self.source}".encode()).hexdigest()[:12]

    def enrich_url(self) -> str:
        return self.direct_url or self.url

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


# ── SOURCES (verified live to return real bid listings to urllib) ─────────────
SOURCES = [
    {"name": "City Bids - Lake Stevens", "url": "https://www.lakestevenswa.gov/Bids.aspx", "city": "Lake Stevens", "county": "Snohomish", "type": "Public Bid", "parser": "civicengage"},
    {"name": "City Bids - Puyallup",     "url": "https://www.cityofpuyallup.org/Bids.aspx", "city": "Puyallup",     "county": "Pierce",    "type": "Public Bid", "parser": "civicengage"},
    {"name": "City Bids - Bothell",      "url": "https://www.bothellwa.gov/Bids.aspx",      "city": "Bothell",      "county": "King",      "type": "Public Bid", "parser": "civicengage"},
    {"name": "City Bids - Marysville",   "url": "https://www.marysvillewa.gov/Bids.aspx",   "city": "Marysville",   "county": "Snohomish", "type": "Public Bid", "parser": "civicengage"},
    {"name": "City Bids - Bremerton",    "url": "https://www.bremertonwa.gov/Bids.aspx",    "city": "Bremerton",    "county": "Kitsap",    "type": "Public Bid", "parser": "civicengage"},
    {"name": "City Bids - Fife",         "url": "https://www.cityoffife.org/Bids.aspx",     "city": "Fife",         "county": "Pierce",    "type": "Public Bid", "parser": "civicengage"},
    {"name": "City Bids - Port Angeles", "url": "https://www.cityofpa.us/Bids.aspx",        "city": "Port Angeles", "county": "Clallam",   "type": "Public Bid", "parser": "civicengage"},
    {"name": "City Bids - Sequim",       "url": "https://www.sequimwa.gov/Bids.aspx",       "city": "Sequim",       "county": "Clallam",   "type": "Public Bid", "parser": "civicengage"},
    {"name": "City Bids - Pasco",        "url": "https://www.pasco-wa.gov/Bids.aspx",        "city": "Pasco",        "county": "Franklin",  "type": "Public Bid", "parser": "civicengage"},
    {"name": "City Bids - Ellensburg",   "url": "https://www.ci.ellensburg.wa.us/Bids.aspx", "city": "Ellensburg",  "county": "Kittitas",  "type": "Public Bid", "parser": "civicengage"},
]

# ── BROWSER (JavaScript) SOURCES — experimental, rendered via Playwright ───────
# These portals are JS single-page apps that return an empty shell to urllib.
# Playwright renders them in a real browser so the HTML can be parsed. This tier
# is EXPERIMENTAL: each portal has a different structure, so expect to tune the
# parser per source after reading the first browser run in the Action logs.
# If Playwright is unavailable or a portal errors, these are skipped silently —
# the verified CivicEngage + permit sources above always still run.
BROWSER_SOURCES = [
    {"name": "City Bids - Bellevue", "url": "https://bellevuewa.gov/city-government/departments/finance/procurement-contracting/current-solicitations",
     "city": "Bellevue", "county": "King", "type": "Public Bid", "engine": "browser", "parser": "generic", "wait_ms": 4500},
    {"name": "King County Procurement", "url": "https://kingcounty.bonfirehub.com/portal/?tab=openOpportunities",
     "city": "Seattle", "county": "King", "type": "Public Bid", "engine": "browser", "parser": "generic", "wait_ms": 6000},
    {"name": "Sound Transit Bids", "url": "https://www.soundtransit.org/business-center/contracting-procurement/contract-opportunities",
     "city": "Seattle", "county": "King", "type": "Public Bid", "engine": "browser", "parser": "generic", "wait_ms": 4500},
    {"name": "WSDOT Bids", "url": "https://wsdot.wa.gov/business-wsdot/contracting-opportunities/construction-contracts/contract-ad-and-award-information",
     "city": "", "county": "", "type": "Public Bid", "engine": "browser", "parser": "generic", "wait_ms": 4500},
]

HIGH_PRIORITY_KW = [
    'seismic', 'retrofit', 'medical', 'hospital', 'clinic', 'parking', 'garage',
    'bridge', 'bridge deck', 'tunnel', 'multifamily', 'apartment', 'campus',
    'university', 'school', 'renovation', 'remodel', 'demolish', 'demolition',
    'concrete', 'structural', 'ground penetrating', 'gpr', 'core drill',
    'anchor', 'rebar', 'post-tension', 'subsurface', 'utility', 'underground',
    'void', 'pavement', 'overlay', 'infrastructure', 'annex', 'addition',
    'sewer', 'storm', 'water main', 'sidewalk', 'street', 'roadway', 'facility',
]
LOW_PRIORITY_KW = [
    'how do i', 'how can i', 'register', 'supplier registration', 'vendor list',
    'bid list', 'previous bid', 'finding bid', 'finding available',
    'learn more', 'doing business',
]
CLOSED_KW = [
    'final bid results', 'bid results', 'bid tabulation', 'notice of award',
    'awarded', 'award notice', 'addendum', 'cancelled', 'canceled',
    'results -', 'rfq results', 'rfp results',
]


# ── HELPERS ───────────────────────────────────────────────────────────────────
def normalize_phone(phone: str) -> str:
    return re.sub(r'\D', '', phone or '')

def is_noise(name: str) -> bool:
    n = (name or '').lower()
    return any(w in n for w in LOW_PRIORITY_KW)

def is_closed(name: str) -> bool:
    n = (name or '').lower()
    return any(w in n for w in CLOSED_KW)

def score_priority(name: str, source: str) -> int:
    n = (name or '').lower()
    if is_noise(n):
        return 1
    score = 6
    if any(w in n for w in HIGH_PRIORITY_KW):
        score += 2
    if re.search(r'(seismic|retrofit|hospital|medical|bridge|parking|garage)', n):
        score += 1
    if re.search(r'(parking.{0,15}structure|garage.{0,15}deck|bridge.{0,10}deck)', n):
        score += 1
    if source.startswith('GC'):
        score += 1
    return min(score, 10)

def clean_text(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip()

def html_to_text(html: str) -> str:
    if not html:
        return ""
    html = re.sub(r'(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>', ' ', html)
    html = re.sub(r'(?i)<br\s*/?>', '\n', html)
    html = re.sub(r'(?i)</(p|div|li|tr|h[1-6])>', '\n', html)
    text = re.sub(r'<[^>]+>', ' ', html)
    for ent, ch in (('&amp;', '&'), ('&nbsp;', ' '), ('&#39;', "'"),
                    ('&quot;', '"'), ('&lt;', '<'), ('&gt;', '>'), ('&#160;', ' ')):
        text = text.replace(ent, ch)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n', text)
    return text.strip()

def log(msg: str):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%S')
    print(f"[{ts}] {msg}")


# ── FETCH (gzip + redirects incl. 308 + lenient TLS) ───────────────────────────
def fetch_url(url: str, timeout: int = 18, _depth: int = 0) -> Optional[str]:
    if _depth > 4:
        return None
    try:
        req = urllib.request.Request(url, headers=HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
            raw = r.read()
            if (r.headers.get('Content-Encoding') or '').lower() == 'gzip':
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            charset = r.headers.get_content_charset() or 'utf-8'
            return raw.decode(charset, errors='replace')
    except urllib.error.HTTPError as e:
        loc = e.headers.get('Location') if e.headers else None
        if e.code in (301, 302, 303, 307, 308) and loc:
            return fetch_url(urljoin(url, loc), timeout, _depth + 1)
        log(f"  fetch HTTP {e.code}: {url[:60]}")
        return None
    except Exception as e:
        log(f"  fetch error {url[:60]}: {e}")
        return None


def fetch_json(url: str):
    txt = fetch_url(url, timeout=25)
    if not txt:
        return None
    try:
        return json.loads(txt)
    except Exception as e:
        log(f"  JSON parse error: {e}")
        return None


_PLAYWRIGHT_STATE = None   # None=untried, True=works, False=unavailable

def fetch_rendered(url: str, wait_ms: int = 4000, wait_selector: str = None) -> Optional[str]:
    """
    Render a JavaScript portal in headless Chromium and return its HTML.
    Requires Playwright + chromium (installed by the workflow). If unavailable
    or the render fails, returns None so the caller simply skips that source —
    this never breaks the urllib-based sources.
    """
    global _PLAYWRIGHT_STATE
    if _PLAYWRIGHT_STATE is False:
        return None
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        if _PLAYWRIGHT_STATE is None:
            log("  (Playwright not installed — browser sources skipped)")
        _PLAYWRIGHT_STATE = False
        return None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
            page = browser.new_page(user_agent=HTTP_HEADERS["User-Agent"])
            page.set_default_timeout(30000)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=15000)
                except Exception:
                    pass
            page.wait_for_timeout(wait_ms)
            html = page.content()
            browser.close()
            _PLAYWRIGHT_STATE = True
            return html
    except Exception as e:
        log(f"  browser render error {url[:50]}: {str(e)[:90]}")
        return None


# ── PARSERS ─────────────────────────────────────────────────────────────────
def parse_civicengage(html: str, source: dict) -> list:
    """Open solicitations on a Granicus/CivicEngage Bids.aspx page (Bids.aspx?bidID=)."""
    leads, seen = [], set()
    pattern = re.compile(
        r'<a[^>]+href=["\']([^"\']*[Bb]ids?\.aspx\?[^"\']*bidID=(\d+)[^"\']*)["\'][^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE)
    for m in pattern.finditer(html):
        href, bid_id, raw_text = m.group(1), m.group(2), m.group(3)
        title = clean_text(re.sub(r'<[^>]+>', '', raw_text))
        title = re.sub(r'(?i)^read\s*on\s*:?\s*', '', title).strip()
        if len(title) < 6 or len(title) > 300 or bid_id in seen:
            continue
        seen.add(bid_id)
        if is_noise(title) or is_closed(title):
            continue
        priority = score_priority(title, source['name'])
        if priority < 3:
            continue
        leads.append(Lead(
            name=title, source=source['name'], url=source['url'],
            direct_url=urljoin(source['url'], href),
            city=source.get('city', ''), county=source.get('county', ''),
            type=source.get('type', 'Public Bid'), bid_number=bid_id, priority=priority,
        ))
    return leads


def parse_generic(html: str, source: dict) -> list:
    """Fallback for any plain-HTML page listing projects as ordinary anchors."""
    leads = []
    pattern = re.compile(r'<a[^>]+href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
    for m in pattern.finditer(html):
        href, raw_text = m.group(1), m.group(2)
        text = clean_text(re.sub(r'<[^>]+>', '', raw_text))
        if len(text) < 8 or len(text) > 300 or is_noise(text) or is_closed(text):
            continue
        has_year = bool(re.search(r'20(2[5-9]|3\d)', text))
        has_bid  = bool(re.search(r'\b(bid|rfp|rfq|contract|project|solicitation)', text.lower()))
        has_kw   = any(k in text.lower() for k in HIGH_PRIORITY_KW)
        if not (has_year or has_bid or has_kw):
            continue
        href = href.strip()
        direct = source['url'] if (not href or href.startswith('#') or
                                    href.lower().startswith('javascript:')) else urljoin(source['url'], href)
        priority = score_priority(text, source['name'])
        if priority < 3:
            continue
        leads.append(Lead(name=text, source=source['name'], url=source['url'],
                          direct_url=direct, city=source.get('city', ''),
                          county=source.get('county', ''), type=source.get('type', 'Public Bid'),
                          priority=priority))
    return leads


def extract_projects_from_html(html: str, source: dict) -> list:
    if source.get('parser') == 'generic':
        return parse_generic(html, source)
    return parse_civicengage(html, source)


def scrape_all_sources() -> list:
    all_leads = []
    for src in SOURCES + BROWSER_SOURCES:
        browser = src.get('engine') == 'browser'
        log(f"Scraping: {src['name']}{' [browser]' if browser else ''}")
        if browser:
            html = fetch_rendered(src['url'], wait_ms=src.get('wait_ms', 4000),
                                  wait_selector=src.get('wait_selector'))
        else:
            html = fetch_url(src['url'])
        if not html:
            continue
        found = extract_projects_from_html(html, src)
        log(f"  → {len(found)} open candidates")
        all_leads.extend(found)
    return all_leads


# ── PERMIT FEEDS (Socrata open data = REAL active construction sites) ─────────
PERMIT_SOURCES = [
    {"name": "Permits - Seattle",
     "endpoint": "https://data.seattle.gov/resource/76t5-zqzr.json",
     "county": "King", "state": "WA", "platform": "socrata", "min_cost": 250000},
]


def collect_permits() -> list:
    leads = []
    for src in PERMIT_SOURCES:
        if src.get("platform") != "socrata":
            continue
        log(f"Permits: {src['name']}")
        where = (
            f"estprojectcost > {src['min_cost']} AND statuscurrent in("
            "'Issued','Under Review','Application Accepted',"
            "'Reviews Completed - Awaiting Issuance','Ready for Issuance',"
            "'Reviews In Process','Scheduled','Phase Issued')"
        )
        url = src["endpoint"] + "?" + urlencode({
            "$where": where,
            "$select": ("permitnum,permitclassmapped,permittypedesc,description,"
                        "estprojectcost,statuscurrent,originaladdress1,originalcity,link"),
            "$order": "estprojectcost DESC",
            "$limit": "300",
        })
        records = fetch_json(url)
        if not records:
            continue
        kept = 0
        for r in records:
            desc = clean_text(r.get("description") or "")
            addr = clean_text(r.get("originaladdress1") or "")
            if len(desc) < 8 or is_noise(desc):
                continue
            try:
                cost = int(float(r.get("estprojectcost") or 0))
            except (TypeError, ValueError):
                cost = 0
            city = (r.get("originalcity") or "").title() or "Seattle"
            klass = clean_text(r.get("permitclassmapped") or "")
            status = clean_text(r.get("statuscurrent") or "")
            link = r.get("link")
            if isinstance(link, dict):
                link = link.get("url", "")
            link = link or src["endpoint"]
            name = (desc[:90] + (f" @ {addr}" if addr else "")).strip()
            priority = score_priority(desc, src["name"])
            if cost >= 50_000_000:
                priority = min(priority + 2, 10)
            elif cost >= 5_000_000:
                priority = min(priority + 1, 10)
            if priority < 3:
                continue
            descparts = [p for p in (addr, f"est ${cost:,}" if cost else "", klass, status) if p]
            leads.append(Lead(
                name=name, source=src["name"], url=src["endpoint"], direct_url=link,
                city=city, county=src.get("county", ""), state=src.get("state", "WA"),
                type="Permit", bid_number=clean_text(r.get("permitnum") or ""),
                description=" · ".join(descparts), priority=priority,
            ))
            kept += 1
        log(f"  → {kept} active construction permits")
    return leads


# ── BID DETAIL PARSING + DETERMINISTIC CONTACT HARVEST ────────────────────────
def parse_bid_detail(page_text: str) -> tuple:
    close_date = ""
    m = re.search(r'(?i)(?:bid\s+)?clos(?:e|ing)\s*(?:date)?(?:\s*/\s*time)?\s*[:\-]?\s*'
                  r'([A-Z][a-z]+\.?\s+\d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4})', page_text)
    if m:
        close_date = clean_text(m.group(1))[:40]
    desc = ""
    m = re.search(r'(?is)description\s*[:\-]?\s*(.{30,400})', page_text)
    if m:
        desc = clean_text(m.group(1))[:300]
    return close_date, desc


_EMAIL_RE = r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.(?:gov|us|org|com|net)'
_JUNK = ('copyright', 'rights', 'reserved', 'privacy', 'sitemap', 'site map',
         'menu', 'login', 'home', 'contact us', 'public records', 'quick links',
         'read on', 'all rights')


def _is_junk(s: str) -> bool:
    s = (s or '').lower()
    return any(j in s for j in _JUNK)


def harvest_contact(page_text: str) -> ContactInfo:
    """
    Deterministically pull the REAL bid contact off the listing page.
    CivicEngage bid pages carry a structured "Contact Person:" block, e.g.:
        Contact Person: Lori Erickson Public Works Analyst lerickson@city.gov
    No AI, no hallucination. Returns blank ContactInfo if nothing usable.
    """
    ci = ContactInfo()
    text = clean_text(page_text)

    # Name/title ONLY from the structured "Contact Person:" block.
    m = re.search(r'(?i)contact\s*person\s*:?\s*(.{3,160}?)'
                  r'(?:related documents|bid opening|publication|category|'
                  r'estimated|status|return method|address|phone|$)', text)
    block = m.group(1).strip(' -·,:') if m else ""
    if block:
        bem = re.search(_EMAIL_RE, block)
        if bem:
            ci.email = bem.group(0).rstrip('.')
        before = (block.split(ci.email)[0] if ci.email and ci.email in block else block).strip(' -·,:')
        nm = re.match(r'([A-Z][a-zA-Z.\-]+\s+[A-Z][a-zA-Z.\-]+)', before)
        if nm and not _is_junk(nm.group(1)):
            ci.name = nm.group(1).strip()
            title = before[len(ci.name):].strip(' -·,:')
            if 2 < len(title) < 70 and not re.search(_EMAIL_RE, title) and not _is_junk(title):
                ci.title = title

    # Email fallback: prefer a purchasing/contract address over a generic one.
    if not ci.email:
        emails = [e.rstrip('.') for e in re.findall(_EMAIL_RE, text) if not _is_junk(e)]
        pref = [e for e in emails if re.search(r'(?i)(contract|purchas|procure|bid|engineer|publicworks|pw|capital)', e)]
        if pref:
            ci.email = pref[0]
        elif emails:
            ci.email = emails[0]

    # Phone: only from the contact block (avoid grabbing a random main line).
    if block:
        pm = (re.search(r'(?i)(?:phone|tel|ph|call)\s*[:.#]?\s*(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]\d{4})', block)
              or re.search(r'(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]\d{4})', block))
        if pm:
            cleaned = re.sub(r'[^\d\s()\-+.]', '', pm.group(1)).strip()
            if 7 <= len(re.sub(r'\D', '', cleaned)) <= 11:
                ci.phone = cleaned
    return ci


# ── AI ENRICHMENT (grounded fallback) ───────────────────────────────────────
def call_claude_api(prompt: str, api_key: str) -> Optional[str]:
    payload = json.dumps({
        "model": AI_MODEL, "max_tokens": 400,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(ANTHROPIC_API_URL, data=payload, method="POST", headers={
        "Content-Type": "application/json", "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            content = json.loads(r.read()).get("content") or [{}]
            return content[0].get("text", "")
    except urllib.error.HTTPError as e:
        log("  AI: 429 rate-limit — skipping" if e.code == 429 else f"  AI: HTTP {e.code}")
        return None
    except Exception as e:
        log(f"  AI: {e}")
        return None


def build_enrichment_prompt(lead: Lead, page_text: str) -> str:
    snippet = (page_text or "").strip()[:PAGE_TEXT_LIMIT]
    whose = lead.source
    if lead.is_gc():
        whose = re.sub(r'^GC\s*[-–—]\s*', '', lead.source).strip()
    return f"""You are extracting contact information for Scan2Core, a GPR scanning and core drilling company in Washington State.

LISTING: {lead.name}
SOURCE: {lead.source}
LOCATION: {lead.city}, {lead.county} County, WA

Below is the visible text of this listing's page. Find the project manager, bid
coordinator, or purchasing/department contact for "{whose}".

ABSOLUTE RULES:
- Use ONLY information that literally appears in the PAGE TEXT below.
- Do NOT use outside knowledge, memory, or guesses. If a detail is not in the text, leave it blank.
- The contact must belong to {whose}, not any other company.
- If the page has no usable contact, return every field blank.

Reply EXACTLY in this format, one field per line, nothing else:
NAME: [first last, or blank]
TITLE: [job title, or blank]
DEPARTMENT: [department, or blank]
EMAIL: [email@domain, or blank]
PHONE: [phone number, or blank]

----- PAGE TEXT -----
{snippet}
----- END PAGE TEXT -----"""


def parse_ai_response(text: str) -> ContactInfo:
    ci = ContactInfo()
    if not text:
        return ci
    blanks = ('blank', 'n/a', 'na', 'none', 'unknown', '', '[blank]', '-')
    for line in text.splitlines():
        if ':' not in line:
            continue
        key, _, val = line.strip().partition(':')
        key = key.strip().upper()
        val = val.strip().strip('[]').strip()
        if val.lower() in blanks:
            continue
        if key == 'NAME':
            ci.name = val
        elif key == 'TITLE':
            ci.title = val
        elif key == 'DEPARTMENT':
            ci.department = val
        elif key == 'EMAIL':
            if '@' in val and '.' in val:
                ci.email = val.split()[0]
        elif key == 'PHONE':
            cleaned = re.sub(r'[^\d\s()\-+.]', '', val).strip()
            if 7 <= len(re.sub(r'\D', '', cleaned)) <= 11:
                ci.phone = cleaned
    return ci


def enrich_lead_with_ai(lead: Lead, api_key: str, page_text: str) -> bool:
    if not page_text or len(page_text.strip()) < 40:
        lead.enriched = "none"
        return False
    text = call_claude_api(build_enrichment_prompt(lead, page_text), api_key)
    if text is None:
        return False
    ci = parse_ai_response(text)
    if ci.has_contact_info():
        lead.contact_name       = ci.name       or ""
        lead.contact_title      = ci.title      or ""
        lead.contact_department = ci.department or ""
        lead.contact_email      = ci.email      or ""
        lead.contact_phone      = ci.phone      or ""
        lead.enriched = "ai" if (ci.email and ci.phone) else "ai_partial"
        return True
    lead.enriched = "none"
    return False


# ── PHONE QUALITY BACKSTOP ──────────────────────────────────────────────────
def build_gc_phone_set(leads: list) -> set:
    return {normalize_phone(l.contact_phone) for l in leads
            if l.is_gc() and normalize_phone(l.contact_phone)}

def build_phone_freq_map(leads: list) -> dict:
    freq = {}
    for l in leads:
        if l.is_gc() or not l.contact_phone:
            continue
        p = normalize_phone(l.contact_phone)
        if p:
            freq[p] = freq.get(p, 0) + 1
    return freq

def _downgrade_after_phone_clear(lead: Lead):
    lead.contact_phone = ""
    if not lead.contact_email and not lead.contact_name:
        lead.enriched = "none"
    elif lead.enriched == "ai":
        lead.enriched = "ai_partial"

def enforce_phone_quality(leads: list) -> int:
    gc_phones = build_gc_phone_set(leads)
    freq = build_phone_freq_map(leads)
    cleared = 0
    for lead in leads:
        if lead.is_gc() or not lead.contact_phone:
            continue
        p = normalize_phone(lead.contact_phone)
        if p in gc_phones:
            log(f"  cleared GC phone from public bid lead: {lead.name[:50]}")
            _downgrade_after_phone_clear(lead); cleared += 1
        elif freq.get(p, 0) >= SHARED_PHONE_THRESHOLD:
            log(f"  cleared shared phone ({freq[p]}x): {lead.name[:50]}")
            _downgrade_after_phone_clear(lead); cleared += 1
    return cleared


# ── DEDUP / PERSISTENCE ─────────────────────────────────────────────────────
def dedup_leads(new_leads: list, existing: list) -> tuple:
    keys = {hashlib.md5(f"{e.get('name','')}|{e.get('source','')}".encode()).hexdigest()[:12]
            for e in existing}
    to_add, to_skip = [], []
    for lead in new_leads:
        (to_skip if lead.lead_id() in keys else to_add).append(lead)
    return to_add, to_skip

def load_existing() -> list:
    if not os.path.exists(OUTPUT_FILE):
        return []
    try:
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        log(f"Warning: could not read {OUTPUT_FILE}: {e}")
        return []

def save_leads(leads: list):
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(leads, f, indent=2, ensure_ascii=False)
    log(f"Saved {len(leads)} leads to {OUTPUT_FILE}")

VALID_ENRICHED = (None, 'ai', 'ai_partial', 'none')

def merge_existing_contact_data(new_lead: Lead, existing: list) -> bool:
    for e in existing:
        if e.get('name') == new_lead.name and e.get('source') == new_lead.source:
            for fld in ('contact_name', 'contact_title', 'contact_department',
                        'contact_email', 'contact_phone', 'close_date', 'description'):
                if e.get(fld):
                    setattr(new_lead, fld, e[fld])
            enr = e.get('enriched')
            new_lead.enriched = enr if enr in VALID_ENRICHED else None
            return True
    return False


# ── BACKFILL ──────────────────────────────────────────────────────────────────
def run_backfill(all_leads: list, api_key: str):
    candidates = [l for l in all_leads if l.enriched is None and not l.is_noise_lead()]
    # Permit detail pages are JavaScript (Accela) and expose no scrapeable
    # contact, so they'd waste every backfill slot (this is what caused the
    # "0 email" run). Mark them processed — their value is the address/project —
    # and spend the whole backfill budget on harvestable bid pages.
    for l in candidates:
        if l.type == "Permit":
            l.enriched = "none"
    unenriched = [l for l in candidates if l.type != "Permit"]
    unenriched.sort(key=lambda l: l.priority, reverse=True)
    targets = unenriched[:BACKFILL_PER_RUN]
    log(f"Backfill: {len(targets)} harvestable bid pages selected "
        f"({sum(1 for l in candidates if l.type=='Permit')} permits skipped — JS, no contact)")

    page_cache = {}
    for i, lead in enumerate(targets):
        log(f"  {i+1}/{len(targets)}: {lead.name[:60]}")
        page_url = lead.enrich_url()
        if page_url not in page_cache:
            html = fetch_url(page_url)
            page_cache[page_url] = html_to_text(html) if html else ""
        page_text = page_cache[page_url]

        if page_text:
            cd, desc = parse_bid_detail(page_text)
            if cd and not lead.close_date:
                lead.close_date = cd
            if desc and not lead.description:
                lead.description = desc

        if not page_text:
            log("  → ✗ no page text (JS-only or unreachable) — left blank")
            lead.enriched = "none"
            continue

        # 1) Deterministic harvest of the structured contact block (no AI).
        ci = harvest_contact(page_text)
        if ci.has_contact_info():
            lead.contact_name  = ci.name  or ""
            lead.contact_title = ci.title or ""
            lead.contact_email = ci.email or ""
            lead.contact_phone = ci.phone or ""
            lead.enriched = "ai" if (ci.email and ci.phone) else "ai_partial"
            log(f"  → ✓ harvested: {(ci.name or ci.email)[:45]}")
            continue

        # 2) Grounded AI fallback only if a key is set and harvest found nothing.
        if not api_key:
            lead.enriched = "none"
            continue
        found = enrich_lead_with_ai(lead, api_key, page_text)
        log(f"  → {'✓ AI contact' if found else '✗ no contact on page'}")
        time.sleep(AI_SLEEP_SECONDS)


def dict_to_lead(e: dict) -> Lead:
    return Lead(
        name=e.get('name', ''), source=e.get('source', ''), url=e.get('url', ''),
        city=e.get('city', ''), county=e.get('county', ''), state=e.get('state', 'WA'),
        priority=e.get('priority', 6), type=e.get('type', 'Public Bid'),
        bid_number=e.get('bid_number', ''), close_date=e.get('close_date', ''),
        description=e.get('description', ''), direct_url=e.get('direct_url', ''),
        found_date=e.get('found_date', '') or _utcnow_iso(),
        contact_name=e.get('contact_name', ''), contact_title=e.get('contact_title', ''),
        contact_department=e.get('contact_department', ''), contact_email=e.get('contact_email', ''),
        contact_phone=e.get('contact_phone', ''),
        enriched=e.get('enriched') if e.get('enriched') in VALID_ENRICHED else None,
    )


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log("WARNING: ANTHROPIC_API_KEY not set — AI fallback disabled (page contacts still harvested)")

    existing = load_existing()
    log(f"Existing leads: {len(existing)}")

    log("── Scraping city bid pages ──")
    raw_leads = scrape_all_sources()
    log("── Permit feeds (active construction) ──")
    raw_leads += collect_permits()
    log(f"Raw candidates: {len(raw_leads)}")

    new_leads, skipped = dedup_leads(raw_leads, existing)
    log(f"New: {len(new_leads)}, Already known: {len(skipped)}")

    for lead in new_leads:
        merge_existing_contact_data(lead, existing)

    existing_leads = []
    for e in existing:
        try:
            existing_leads.append(dict_to_lead(e))
        except Exception as ex:
            log(f"  skip malformed existing lead: {ex}")

    all_leads = existing_leads + new_leads
    log(f"Total after merge: {len(all_leads)}")

    log("── Contact backfill (harvest + grounded AI) ──")
    run_backfill(all_leads, api_key)

    log("── Phone quality pass ──")
    log(f"Cleared {enforce_phone_quality(all_leads)} suspect phones")

    all_leads.sort(key=lambda l: ((l.priority or 0), l.found_date or ''), reverse=True)
    save_leads([l.to_dict() for l in all_leads])

    total     = len(all_leads)
    has_email = sum(1 for l in all_leads if l.contact_email)
    has_phone = sum(1 for l in all_leads if l.contact_phone)
    permits   = sum(1 for l in all_leads if l.type == "Permit")
    hot       = sum(1 for l in all_leads if (l.priority or 0) >= 9)
    log(f"── Summary: {total} leads | {hot} hot | {permits} permits | {has_email} email | {has_phone} phone ──")


if __name__ == "__main__":
    main()
