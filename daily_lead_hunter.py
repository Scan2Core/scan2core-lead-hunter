"""
daily_lead_hunter.py — Scan2Core Lead Hunter (GitHub Actions daily scraper)
Scrapes WA State construction bid pages, deduplicates, priority-scores, and
enriches leads with contact info that is EXTRACTED FROM THE LEAD'S OWN PAGE.

WHY THIS VERSION EXISTS
-----------------------
The previous enrichment asked the model to *recall* a project's contact info
from memory. A model has no reliable memory of a specific bid's project
manager, so it filled the gaps with plausible-but-wrong data — most visibly by
stamping Sellen's main line (206-682-7770) onto every lead it had seen during
training. Phone de-duplication was bolted on afterward to scrub that garbage.

This version removes the root cause instead of scrubbing the symptom:

  1. For each lead we FETCH the lead's actual page (direct_url, then url).
  2. We strip it to visible text.
  3. We ask the model to return ONLY contact details that literally appear in
     that text. If the page contains no contact, the model returns blank.

Result: the model extracts real contacts that are on the page (e.g. an
agency project manager named in a bid notice, or a GC's office number in a
site footer) and returns nothing when the page has nothing. No recall, no
hallucination. JS-rendered portals that return an empty shell simply yield a
blank lead — honest, not fabricated.

PHONE QUALITY POLICY (defense-in-depth, kept as a backstop)
-----------------------------------------------------------
  - GC Portfolio leads (source starts with "GC"): the GC's own office number,
    if it appears on the GC's page, is legitimate and is saved as-is.
  - Public Bid leads: any phone that ALSO appears on a GC lead is treated as
    cross-contamination and cleared.
  - Any phone appearing on SHARED_PHONE_THRESHOLD+ non-GC leads is treated as a
    shared main line and cleared from those non-GC leads.
"""

import json, os, re, time, datetime, hashlib
import urllib.request, urllib.error
from urllib.parse import urljoin
from dataclasses import dataclass, field, asdict
from typing import Optional

# ── CONFIG ────────────────────────────────────────────────────────────────────
AI_MODEL          = "claude-haiku-4-5-20251001"
AI_SLEEP_SECONDS  = 2        # pause between AI calls to avoid 429s
BACKFILL_PER_RUN  = 15       # max unenriched leads to process per run
OUTPUT_FILE       = "found_projects.json"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
PAGE_TEXT_LIMIT   = 6000     # chars of page text sent to the model per lead

# Threshold: a phone on this many non-GC leads = shared main line / bad data
SHARED_PHONE_THRESHOLD = 5


# ── DATE HELPER (timezone-aware; utcnow() is deprecated in 3.12+) ───────────────
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

    def is_empty(self) -> bool:
        return not self.has_contact_info()


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
        """GC Portfolio leads have source starting with 'GC' + a dash/space."""
        return re.match(r'^GC[\s\-–—]', self.source or '') is not None

    def is_noise_lead(self) -> bool:
        return any(w in (self.name or '').lower() for w in LOW_PRIORITY_KW)

    def lead_id(self) -> str:
        raw = f"{self.name}|{self.source}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def enrich_url(self) -> str:
        """Page to fetch for contact extraction: the specific bid page first."""
        return self.direct_url or self.url

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


# ── SOURCES ───────────────────────────────────────────────────────────────────
# Each source is a dict with keys: name, url, city, county, type
# "type" is "Public Bid" or "GC Portfolio".  GC names start with "GC – ".
SOURCES = [
    # ── King County / Seattle ────────────────────────────────
    {"name": "Seattle DPD/DCI Bids",     "url": "https://www.seattle.gov/city-purchasing-and-contracting/bid-opportunities", "city": "Seattle",   "county": "King",      "type": "Public Bid"},
    {"name": "Seattle Consultant Connection", "url": "https://consultants.seattle.gov",                                       "city": "Seattle",   "county": "King",      "type": "Public Bid"},
    {"name": "King County Procurement",  "url": "https://www.kingcounty.gov/tools/procurement/Bids.aspx",                     "city": "Seattle",   "county": "King",      "type": "Public Bid"},
    {"name": "City Bids - Bellevue",     "url": "https://www.bellevuewa.gov/city-government/departments/finance/purchasing", "city": "Bellevue",  "county": "King",      "type": "Public Bid"},
    {"name": "City Bids - Redmond",      "url": "https://www.redmondwa.gov/331/Purchasing-Bids",                              "city": "Redmond",   "county": "King",      "type": "Public Bid"},
    {"name": "City Bids - Kirkland",     "url": "https://www.kirklandwa.gov/Government/Departments/Finance/Purchasing-Bids", "city": "Kirkland",  "county": "King",      "type": "Public Bid"},
    {"name": "City Bids - Renton",       "url": "https://rentonwa.gov/city_hall/administrative_services/purchasing_bids",    "city": "Renton",    "county": "King",      "type": "Public Bid"},
    {"name": "City Bids - Kent",         "url": "https://www.kentwa.gov/departments/finance/purchasing-and-bids",            "city": "Kent",      "county": "King",      "type": "Public Bid"},
    {"name": "City Bids - Auburn",       "url": "https://www.auburnwa.gov/city_hall/departments/finance/procurement",       "city": "Auburn",    "county": "King",      "type": "Public Bid"},
    {"name": "City Bids - Shoreline",    "url": "https://www.shorelinewa.gov/government/departments/city-manager-s-office/purchasing", "city": "Shoreline", "county": "King", "type": "Public Bid"},
    {"name": "City Bids - Burien",       "url": "https://burienwa.gov/business/purchasing_and_bids",                        "city": "Burien",    "county": "King",      "type": "Public Bid"},
    {"name": "City Bids - Federal Way",  "url": "https://www.cityoffederalway.com/purchasing",                               "city": "Federal Way","county": "King",     "type": "Public Bid"},
    # ── Pierce County ────────────────────────────────────────
    {"name": "Pierce County Procurement","url": "https://www.piercecountywa.gov/1289/Purchasing-and-Contracting",            "city": "Tacoma",    "county": "Pierce",    "type": "Public Bid"},
    {"name": "City Bids - Tacoma",       "url": "https://www.cityoftacoma.org/government/city_departments/purchasing/bids_and_proposals", "city": "Tacoma", "county": "Pierce", "type": "Public Bid"},
    # ── Snohomish County ─────────────────────────────────────
    {"name": "Snohomish County PW",      "url": "https://www.snohomishcountywa.gov/2195/Bid-Openings",                       "city": "Everett",   "county": "Snohomish", "type": "Public Bid"},
    {"name": "City Bids - Everett",      "url": "https://www.everettwa.gov/319/Procurement",                                 "city": "Everett",   "county": "Snohomish", "type": "Public Bid"},
    {"name": "City Bids - Marysville",   "url": "https://www.marysvillewa.gov/government/city_departments/public_works/bids_rfps", "city": "Marysville", "county": "Snohomish", "type": "Public Bid"},
    # ── Kitsap County ────────────────────────────────────────
    {"name": "City Bids - Kitsap County PW", "url": "https://www.kitsap.gov/publicworks/Pages/Bids.aspx",                   "city": "Bremerton", "county": "Kitsap",    "type": "Public Bid"},
    {"name": "City Bids - Bremerton",    "url": "https://www.ci.bremerton.wa.us/288/Purchasing-Bids",                        "city": "Bremerton", "county": "Kitsap",    "type": "Public Bid"},
    # ── Thurston County ──────────────────────────────────────
    {"name": "City Bids - Olympia",      "url": "https://www.olympiawa.gov/government/departments/public-works/engineering-and-capital-projects", "city": "Olympia", "county": "Thurston", "type": "Public Bid"},
    # ── State of Washington ──────────────────────────────────
    {"name": "WSDOT Bids",               "url": "https://www.wsdot.wa.gov/Business/Construction/",                           "city": "",          "county": "",          "type": "Public Bid"},
    {"name": "WA DES Bids",              "url": "https://des.wa.gov/services/contracting-purchasing/public-works-engineering/advertised-public-works-projects", "city": "", "county": "", "type": "Public Bid"},
    {"name": "Sound Transit Bids",       "url": "https://www.soundtransit.org/business-center/contracting-procurement",     "city": "Seattle",   "county": "King",      "type": "Public Bid"},
    {"name": "Port of Seattle Bids",     "url": "https://www.portseattle.org/business/doing-business-port/contracting-opportunities", "city": "Seattle", "county": "King", "type": "Public Bid"},
    # ── GC Portfolio ─────────────────────────────────────────
    {"name": "GC – Sellen Construction", "url": "https://www.sellen.com/projects/",                                          "city": "Seattle",   "county": "King",      "type": "GC Portfolio"},
    {"name": "GC – Skanska USA",         "url": "https://www.skanska.com/en/markets/usa/projects/",                          "city": "Seattle",   "county": "King",      "type": "GC Portfolio"},
    {"name": "GC – Mortenson",           "url": "https://www.mortenson.com/projects",                                        "city": "Seattle",   "county": "King",      "type": "GC Portfolio"},
    {"name": "GC – Lease Crutcher Lewis","url": "https://www.lewisbuilds.com/projects/",                                     "city": "Seattle",   "county": "King",      "type": "GC Portfolio"},
    {"name": "GC – Turner Construction", "url": "https://www.turnerconstruction.com/projects",                               "city": "Seattle",   "county": "King",      "type": "GC Portfolio"},
    {"name": "GC – Hensel Phelps",       "url": "https://www.henselphelps.com/projects/",                                    "city": "Seattle",   "county": "King",      "type": "GC Portfolio"},
]

# Keywords that make a project HIGH priority for GPR/Core work
HIGH_PRIORITY_KW = [
    'seismic', 'retrofit', 'medical', 'hospital', 'clinic', 'parking', 'garage',
    'bridge', 'bridge deck', 'tunnel', 'multifamily', 'apartment', 'campus',
    'university', 'school', 'renovation', 'remodel', 'demolish', 'demolition',
    'concrete', 'structural', 'ground penetrating', 'gpr', 'core drill',
    'anchor', 'rebar', 'post-tension', 'subsurface', 'utility', 'underground',
    'void', 'pavement', 'overlay', 'infrastructure', 'annex', 'addition',
]
LOW_PRIORITY_KW = [
    'how do i', 'how can i', 'register', 'supplier registration', 'vendor list',
    'bid list', 'previous bid', 'bid tabulation', 'finding bid', 'finding available',
    '2024 bid', '2025 bid', 'learn more', 'doing business',
]


# ── HELPERS ───────────────────────────────────────────────────────────────────
def normalize_phone(phone: str) -> str:
    """Strip all non-digits for comparison."""
    return re.sub(r'\D', '', phone or '')

def score_priority(name: str, source: str) -> int:
    n = name.lower()
    if any(w in n for w in LOW_PRIORITY_KW):
        return 1
    score = 6
    if any(w in n for w in HIGH_PRIORITY_KW):
        score += 2
    if re.search(r'(seismic|retrofit|hospital|medical)', n):
        score += 1
    if re.search(r'(parking.{0,15}structure|garage.{0,15}deck)', n):
        score += 1
    if source.startswith('GC'):
        score += 1
    return min(score, 10)

def is_noise(name: str) -> bool:
    return any(w in (name or '').lower() for w in LOW_PRIORITY_KW)

def clean_text(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip()

def html_to_text(html: str) -> str:
    """Strip scripts/styles and all tags, returning collapsed visible text."""
    if not html:
        return ""
    html = re.sub(r'(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>', ' ', html)
    html = re.sub(r'(?i)<br\s*/?>', '\n', html)
    html = re.sub(r'(?i)</(p|div|li|tr|h[1-6])>', '\n', html)
    text = re.sub(r'<[^>]+>', ' ', html)
    # Decode a handful of common entities; leave the rest harmlessly intact.
    for ent, ch in (('&amp;', '&'), ('&nbsp;', ' '), ('&#39;', "'"),
                    ('&quot;', '"'), ('&lt;', '<'), ('&gt;', '>')):
        text = text.replace(ent, ch)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n', text)
    return text.strip()

def log(msg: str):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%S')
    print(f"[{ts}] {msg}")


# ── SCRAPER ───────────────────────────────────────────────────────────────────
def fetch_url(url: str, timeout: int = 15) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; Scan2Core-LeadHunter/1.0; +https://scan2core.github.io)',
            'Accept': 'text/html,application/xhtml+xml,*/*',
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            charset = r.headers.get_content_charset() or 'utf-8'
            return r.read().decode(charset, errors='replace')
    except Exception as e:
        log(f"  fetch error {url[:60]}: {e}")
        return None


def extract_projects_from_html(html: str, source: dict) -> list:
    """
    Lightweight HTML scraper: finds anchor tags that look like bid/project
    titles and returns them as Lead objects.  Covers the common table/list
    pattern across WA agency sites.
    """
    leads = []
    pattern = re.compile(r'<a[^>]+href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
    for m in pattern.finditer(html):
        href, raw_text = m.group(1), m.group(2)
        text = clean_text(re.sub(r'<[^>]+>', '', raw_text))
        if len(text) < 8 or len(text) > 300:
            continue
        if is_noise(text):
            continue
        # Keep links that look like project/bid titles
        has_year = bool(re.search(r'20(2[4-9]|3\d)', text))
        has_bid  = bool(re.search(r'\b(bid|rfp|rfq|contract|project|cp\d|pw\d)', text.lower()))
        has_kw   = any(k in text.lower() for k in HIGH_PRIORITY_KW)
        if not (has_year or has_bid or has_kw):
            continue

        # Build absolute URL correctly (handles root-relative "/foo" hrefs).
        href = href.strip()
        if not href or href.startswith('#') or href.lower().startswith('javascript:'):
            direct = source['url']
        else:
            direct = urljoin(source['url'], href)

        bid_num = ''
        bn = re.search(r'\b([A-Z]{1,4}[-_]?\d{4}[-_]\d{2,6}|\d{4}[-_]\d{2,5})\b', text)
        if bn:
            bid_num = bn.group(1)

        priority = score_priority(text, source['name'])
        if priority < 3:
            continue

        leads.append(Lead(
            name        = text,
            source      = source['name'],
            url         = source['url'],
            direct_url  = direct,
            city        = source.get('city', ''),
            county      = source.get('county', ''),
            type        = source.get('type', 'Public Bid'),
            bid_number  = bid_num,
            priority    = priority,
        ))
    return leads


def scrape_all_sources() -> list:
    all_leads = []
    for src in SOURCES:
        log(f"Scraping: {src['name']}")
        html = fetch_url(src['url'])
        if not html:
            continue
        found = extract_projects_from_html(html, src)
        log(f"  → {len(found)} candidates")
        all_leads.extend(found)
    return all_leads


# ── AI ENRICHMENT (grounded: extract from page text only) ──────────────────────
def call_claude_api(prompt: str, api_key: str) -> Optional[str]:
    """POST to the Anthropic Messages API. Returns assistant text or None."""
    payload = json.dumps({
        "model": AI_MODEL,
        "max_tokens": 400,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data    = payload,
        method  = "POST",
        headers = {
            "Content-Type":      "application/json",
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
            content = data.get("content") or [{}]
            return content[0].get("text", "")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            log("  AI: 429 rate-limit — skipping this lead")
        else:
            log(f"  AI: HTTP {e.code}")
        return None
    except Exception as e:
        log(f"  AI: {e}")
        return None


def build_enrichment_prompt(lead: Lead, page_text: str) -> str:
    """
    Grounded extraction prompt. The model may ONLY use the page text provided.
    This is what stops hallucinated contacts (e.g. the recurring 206-682-7770).
    """
    snippet = (page_text or "").strip()[:PAGE_TEXT_LIMIT]

    if lead.is_gc():
        gc_name = re.sub(r'^GC\s*[-–—]\s*', '', lead.source).strip()
        who = (f"the {gc_name} office contact (project manager, estimator, "
               f"subcontractor/preconstruction coordinator, or the company's "
               f"main office phone/email) for a subcontractor wanting to bid")
        whose = gc_name
    else:
        who = (f"the project manager, bid coordinator, or purchasing contact "
               f"for the agency \"{lead.source}\"")
        whose = lead.source

    return f"""You are extracting contact information for Scan2Core, a GPR scanning and core drilling company in Washington State.

PROJECT / LISTING: {lead.name}
SOURCE: {lead.source}
LOCATION: {lead.city}, {lead.county} County, WA

Below is the visible text of the page for this listing. Find {who}.

ABSOLUTE RULES — read carefully:
- Use ONLY information that literally appears in the PAGE TEXT below.
- Do NOT use any outside knowledge, memory, or guesses. If a detail is not in the text, leave that field blank.
- The contact must belong to {whose}. Do NOT return a number for any other company.
- If the page has no usable contact, return every field blank. Blank is the correct, expected answer for many pages.

Reply in EXACTLY this format, one field per line, nothing else:
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
        line = line.strip()
        if ':' not in line:
            continue
        key, _, val = line.partition(':')
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
            digits = re.sub(r'\D', '', cleaned)
            if 7 <= len(digits) <= 11:
                ci.phone = cleaned
    return ci


def enrich_lead_with_ai(lead: Lead, api_key: str, page_text: str) -> bool:
    """
    Extract contact info for a lead from its own page text.
    If the page has no usable text, we do NOT call the model (nothing to
    ground on) and mark the lead 'none'. Returns True if contact info found.
    """
    if not page_text or len(page_text.strip()) < 40:
        lead.enriched = "none"
        return False

    text = call_claude_api(build_enrichment_prompt(lead, page_text), api_key)
    if text is None:
        return False  # transient error (e.g. 429): leave unenriched for next run

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


# ── PHONE QUALITY ENFORCEMENT (backstop) ───────────────────────────────────────
def build_gc_phone_set(leads: list) -> set:
    gc_phones = set()
    for lead in leads:
        if lead.is_gc() and lead.contact_phone:
            n = normalize_phone(lead.contact_phone)
            if n:
                gc_phones.add(n)
    return gc_phones


def build_phone_freq_map(leads: list) -> dict:
    """Count how many NON-GC leads each phone appears on."""
    freq = {}
    for lead in leads:
        if lead.is_gc() or not lead.contact_phone:
            continue
        p = normalize_phone(lead.contact_phone)
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
    """
    1. Clear phones from non-GC leads that also appear on a GC lead.
    2. Clear phones from non-GC leads that appear on SHARED_PHONE_THRESHOLD+
       non-GC leads (a shared main line is not a project contact).
    Returns number of phones cleared.
    """
    gc_phones = build_gc_phone_set(leads)
    phone_freq = build_phone_freq_map(leads)
    cleared = 0
    for lead in leads:
        if lead.is_gc() or not lead.contact_phone:
            continue
        p = normalize_phone(lead.contact_phone)
        if p in gc_phones:
            log(f"  cleared GC phone from public bid lead: {lead.name[:50]}")
            _downgrade_after_phone_clear(lead)
            cleared += 1
            continue
        if phone_freq.get(p, 0) >= SHARED_PHONE_THRESHOLD:
            log(f"  cleared shared phone ({phone_freq[p]}x): {lead.name[:50]}")
            _downgrade_after_phone_clear(lead)
            cleared += 1
    return cleared


# ── DEDUPLICATION ─────────────────────────────────────────────────────────────
def dedup_leads(new_leads: list, existing: list) -> tuple:
    """Return (to_add, to_skip). Dedup key = MD5(name|source)[:12]."""
    existing_keys = set()
    for e in existing:
        key = hashlib.md5(f"{e.get('name','')}|{e.get('source','')}".encode()).hexdigest()[:12]
        existing_keys.add(key)

    to_add, to_skip = [], []
    for lead in new_leads:
        (to_skip if lead.lead_id() in existing_keys else to_add).append(lead)
    return to_add, to_skip


# ── PERSISTENCE ───────────────────────────────────────────────────────────────
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
    """
    If a re-found lead (same name+source) already had contact data, copy it so
    we don't re-spend API calls. 'enriched' is normalized to a valid value.
    """
    for e in existing:
        if e.get('name') == new_lead.name and e.get('source') == new_lead.source:
            for fld in ('contact_name', 'contact_title', 'contact_department',
                        'contact_email', 'contact_phone'):
                if e.get(fld):
                    setattr(new_lead, fld, e[fld])
            enr = e.get('enriched')
            new_lead.enriched = enr if enr in VALID_ENRICHED else None
            return True
    return False


# ── BACKFILL ENRICHMENT LOOP ──────────────────────────────────────────────────
def run_backfill(all_leads: list, api_key: str):
    """
    Enrich up to BACKFILL_PER_RUN unenriched, non-noise leads (highest priority
    first). Each lead's page is fetched once; pages are cached within the run so
    leads sharing a source page don't refetch.
    """
    unenriched = [l for l in all_leads if l.enriched is None and not l.is_noise_lead()]
    unenriched.sort(key=lambda l: l.priority, reverse=True)
    targets = unenriched[:BACKFILL_PER_RUN]
    log(f"Backfill: {len(targets)} of {len(unenriched)} unenriched leads selected")

    page_cache = {}
    for i, lead in enumerate(targets):
        log(f"  AI {i+1}/{len(targets)}: {lead.name[:60]}")
        page_url = lead.enrich_url()
        if page_url not in page_cache:
            html = fetch_url(page_url)
            page_cache[page_url] = html_to_text(html) if html else ""
        page_text = page_cache[page_url]

        if not page_text:
            log("  → ✗ no page text (JS-only or unreachable) — left blank")
            lead.enriched = "none"
            continue

        found = enrich_lead_with_ai(lead, api_key, page_text)
        log(f"  → {'✓ contact extracted' if found else '✗ no contact on page'}")
        time.sleep(AI_SLEEP_SECONDS)


# ── MAIN ──────────────────────────────────────────────────────────────────────
def dict_to_lead(e: dict) -> Lead:
    return Lead(
        name               = e.get('name', ''),
        source             = e.get('source', ''),
        url                = e.get('url', ''),
        city               = e.get('city', ''),
        county             = e.get('county', ''),
        state              = e.get('state', 'WA'),
        priority           = e.get('priority', 6),
        type               = e.get('type', 'Public Bid'),
        bid_number         = e.get('bid_number', ''),
        close_date         = e.get('close_date', ''),
        description        = e.get('description', ''),
        direct_url         = e.get('direct_url', ''),
        found_date         = e.get('found_date', '') or _utcnow_iso(),
        contact_name       = e.get('contact_name', ''),
        contact_title      = e.get('contact_title', ''),
        contact_department = e.get('contact_department', ''),
        contact_email      = e.get('contact_email', ''),
        contact_phone      = e.get('contact_phone', ''),
        enriched           = e.get('enriched') if e.get('enriched') in VALID_ENRICHED else None,
    )


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log("WARNING: ANTHROPIC_API_KEY not set — AI enrichment disabled")

    # 1. Load existing data
    existing = load_existing()
    log(f"Existing leads: {len(existing)}")

    # 2. Scrape all sources
    log("── Scraping sources ──")
    raw_leads = scrape_all_sources()
    log(f"Raw candidates: {len(raw_leads)}")

    # 3. Dedup against existing
    new_leads, skipped = dedup_leads(raw_leads, existing)
    log(f"New: {len(new_leads)}, Already known: {len(skipped)}")

    # 4. Restore prior contact data onto re-found leads
    for lead in new_leads:
        merge_existing_contact_data(lead, existing)

    # 5. Rebuild full list: existing (dicts → Lead) + new
    existing_leads = []
    for e in existing:
        try:
            existing_leads.append(dict_to_lead(e))
        except Exception as ex:
            log(f"  skip malformed existing lead: {ex}")

    all_leads = existing_leads + new_leads
    log(f"Total after merge: {len(all_leads)}")

    # 6. AI backfill (grounded extraction)
    if api_key:
        log("── AI enrichment (grounded) ──")
        run_backfill(all_leads, api_key)

    # 7. Phone quality backstop
    log("── Phone quality pass ──")
    cleared = enforce_phone_quality(all_leads)
    log(f"Cleared {cleared} suspect phones")

    # 8. Sort: priority desc, then newest first
    all_leads.sort(key=lambda l: ((l.priority or 0), l.found_date or ''), reverse=True)

    # 9. Save
    save_leads([l.to_dict() for l in all_leads])

    # 10. Summary
    total     = len(all_leads)
    has_email = sum(1 for l in all_leads if l.contact_email)
    has_phone = sum(1 for l in all_leads if l.contact_phone)
    gc_leads  = sum(1 for l in all_leads if l.is_gc())
    hot       = sum(1 for l in all_leads if (l.priority or 0) >= 9)
    log(f"── Summary: {total} leads | {hot} hot | {gc_leads} GC | {has_email} email | {has_phone} phone ──")


if __name__ == "__main__":
    main()
