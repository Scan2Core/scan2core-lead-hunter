"""
daily_lead_hunter.py — Scan2Core Lead Hunter (GitHub Actions daily scraper)
Scrapes WA State construction bid pages, deduplicates, priority-scores, and
enriches leads with AI-sourced contact info.  Outputs found_projects.json.

PHONE QUALITY POLICY:
  - GC Portfolio leads (source starts with "GC"): AI may return the GC's main
    office number. That's expected — it's saved as-is.
  - Public Bid leads (city/county/state agencies): AI must ONLY return the
    specific bid agency's project contact. Any phone that also appears on a GC
    Portfolio lead is rejected as cross-contamination.
  - After enrichment, any phone appearing on 5+ leads (across all types) is
    flagged as a likely main-line number and cleared from non-GC leads.
"""

import json, os, re, time, datetime, hashlib, random, urllib.request, urllib.error
from dataclasses import dataclass, field, asdict
from typing import Optional

# ── CONFIG ────────────────────────────────────────────────────────────────────
AI_MODEL          = "claude-haiku-4-5-20251001"
AI_SLEEP_SECONDS  = 2       # pause between AI calls to avoid 429s
BACKFILL_PER_RUN  = 15      # max unenriched leads to process per run
OUTPUT_FILE       = "found_projects.json"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

# Threshold: a phone on this many leads = GC main line / hallucination
SHARED_PHONE_THRESHOLD = 5

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
    found_date:         str  = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    contact_name:       str  = ""
    contact_title:      str  = ""
    contact_department: str  = ""
    contact_email:      str  = ""
    contact_phone:      str  = ""
    enriched:           Optional[str] = None   # None | 'scraped' | 'ai' | 'ai_partial' | 'none'

    def is_gc(self) -> bool:
        """GC Portfolio leads have source starting with 'GC'."""
        return re.match(r'^GC[\s\-–—]', self.source or '') is not None

    def lead_id(self) -> str:
        raw = f"{self.name}|{self.source}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        d = asdict(self)
        # Remove None values to keep JSON clean
        return {k: v for k, v in d.items() if v is not None}


# ── SOURCES ───────────────────────────────────────────────────────────────────
# Each source is a dict with keys: name, url, city, county, type
# "type" is "Public Bid" or "GC Portfolio"
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
    {"name": "City Bids - Shoreline",    "url": "https://www.shorelinewa.gov/business/bids-rfps",                            "city": "Shoreline", "county": "King",      "type": "Public Bid"},
    {"name": "City Bids - Burien",       "url": "https://www.burienwa.gov/city_hall/working_with_us/bids_rfp_rfq",          "city": "Burien",    "county": "King",      "type": "Public Bid"},
    {"name": "City Bids - Federal Way",  "url": "https://www.cityoffederalway.com/bids",                                    "city": "Federal Way","county": "King",      "type": "Public Bid"},
    # ── Pierce County ────────────────────────────────────────
    {"name": "Pierce County Procurement","url": "https://www.piercecountywa.gov/7829/Current-Solicitations",                "city": "Tacoma",    "county": "Pierce",    "type": "Public Bid"},
    {"name": "City Bids - Tacoma",       "url": "https://www.cityoftacoma.org/government/city_departments/purchasing/bids_and_proposals", "city": "Tacoma", "county": "Pierce", "type": "Public Bid"},
    # ── Snohomish County ─────────────────────────────────────
    {"name": "Snohomish County PW",      "url": "https://www.snohomishcountywa.gov/2195/Bid-Openings",                       "city": "Everett",   "county": "Snohomish", "type": "Public Bid"},
    {"name": "City Bids - Everett",      "url": "https://www.everettwa.gov/319/Procurement",                                 "city": "Everett",   "county": "Snohomish", "type": "Public Bid"},
    {"name": "City Bids - Marysville",   "url": "https://www.marysvillewa.gov/936/Bid-opportunities",                        "city": "Marysville","county": "Snohomish", "type": "Public Bid"},
    # ── Kitsap County ────────────────────────────────────────
    {"name": "City Bids - Kitsap County PW", "url": "https://www.kitsap.gov/das/pages/online-bids.aspx",                   "city": "Bremerton", "county": "Kitsap",    "type": "Public Bid"},
    {"name": "City Bids - Bremerton",    "url": "https://www.bremertonwa.gov/Bids.aspx",                                    "city": "Bremerton", "county": "Kitsap",    "type": "Public Bid"},
    # ── Thurston County ──────────────────────────────────────
    {"name": "City Bids - Olympia",      "url": "https://www.olympiawa.gov/government/contracts___purchasing/bids.php",     "city": "Olympia",   "county": "Thurston",  "type": "Public Bid"},
    # ── State of Washington ──────────────────────────────────
    {"name": "WSDOT Bids",               "url": "https://wsdot.wa.gov/business-wsdot/contracting-opportunities",            "city": "",          "county": "",          "type": "Public Bid"},
    {"name": "WA DES Bids",              "url": "https://apps.des.wa.gov/EASBids/",                                         "city": "",          "county": "",          "type": "Public Bid"},
    {"name": "Sound Transit Bids",       "url": "https://www.soundtransit.org/get-to-know-us/doing-business-with-us/procurement-contracts-agreements", "city": "Seattle", "county": "King", "type": "Public Bid"},
    {"name": "Port of Seattle Bids",     "url": "https://www.portseattle.org/business/bid-opportunities",                   "city": "Seattle",   "county": "King",      "type": "Public Bid"},
    # ── GC Portfolio ─────────────────────────────────────────
    {"name": "GC – Sellen Construction", "url": "https://www.sellen.com/projects/",                                          "city": "Seattle",   "county": "King",      "type": "GC Portfolio"},
    {"name": "GC – Skanska USA",         "url": "https://www.usa.skanska.com/what-we-deliver/projects/",                    "city": "Seattle",   "county": "King",      "type": "GC Portfolio"},
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
    n = name.lower()
    return any(w in n for w in LOW_PRIORITY_KW)

def clean_text(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()

def log(msg: str):
    ts = datetime.datetime.utcnow().strftime('%H:%M:%S')
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

def extract_projects_from_html(html: str, source: dict) -> list[Lead]:
    """
    Very lightweight HTML scraper: finds anchor tags that look like
    bid/project titles and returns them as Lead objects.
    Real production scrape would use source-specific parsers; this
    covers the common table/list pattern across WA agency sites.
    """
    leads = []
    # Find all <a> text + href pairs
    pattern = re.compile(r'<a[^>]+href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
    for m in pattern.finditer(html):
        href, raw_text = m.group(1), m.group(2)
        text = clean_text(re.sub(r'<[^>]+>', '', raw_text))
        if len(text) < 8 or len(text) > 300:
            continue
        if is_noise(text):
            continue
        # Only keep links that look like project/bid titles
        # (contain year, bid#, or construction-related keywords)
        has_year = bool(re.search(r'20(2[4-9]|3\d)', text))
        has_bid  = bool(re.search(r'\b(bid|rfp|rfq|contract|project|cp\d|pw\d)', text.lower()))
        has_kw   = any(k in text.lower() for k in HIGH_PRIORITY_KW)
        if not (has_year or has_bid or has_kw):
            continue

        # Build absolute URL
        direct = href if href.startswith('http') else (source['url'].rstrip('/') + '/' + href.lstrip('/'))

        # Extract bid number if present
        bid_num = ''
        bn = re.search(r'\b([A-Z]{1,4}[-_]?\d{4}[-_]\d{2,6}|\d{4}[-_]\d{2,5})\b', text)
        if bn:
            bid_num = bn.group(1)

        priority = score_priority(text, source['name'])
        if priority < 3:
            continue

        lead = Lead(
            name        = text,
            source      = source['name'],
            url         = source['url'],
            direct_url  = direct,
            city        = source.get('city', ''),
            county      = source.get('county', ''),
            type        = source.get('type', 'Public Bid'),
            bid_number  = bid_num,
            priority    = priority,
        )
        leads.append(lead)
    return leads


def scrape_all_sources() -> list[Lead]:
    all_leads: list[Lead] = []
    for src in SOURCES:
        log(f"Scraping: {src['name']}")
        html = fetch_url(src['url'])
        if not html:
            continue
        found = extract_projects_from_html(html, src)
        log(f"  → {len(found)} candidates")
        all_leads.extend(found)
    return all_leads


# ── AI ENRICHMENT ─────────────────────────────────────────────────────────────
def call_claude_api(prompt: str, api_key: str) -> Optional[str]:
    """
    POST to Anthropic Messages API.  Returns the assistant text or None.
    No `tools` parameter — stripped to avoid rate-limit issues on Haiku.
    """
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
            return data.get("content", [{}])[0].get("text", "")
    except urllib.error.HTTPError as e:
        code = e.code
        if code == 429:
            log("  AI: 429 rate-limit — skipping this lead")
            return None
        log(f"  AI: HTTP {code}")
        return None
    except Exception as e:
        log(f"  AI: {e}")
        return None


def build_enrichment_prompt(lead: Lead) -> str:
    """
    Separate prompts for GC Portfolio vs Public Bid leads.

    GC Portfolio: ask for GC's main subcontractor contact / project manager.
    Public Bid:   ask ONLY for the bid agency's project contact.
                  Explicitly forbid returning general contractor phone numbers.
    """
    if lead.is_gc():
        gc_name = re.sub(r'^GC\s*[-–—]\s*', '', lead.source).strip()
        return f"""You are a construction industry researcher helping Scan2Core (a GPR scanning and core drilling company in Washington State) find contact information.

PROJECT: {lead.name}
GC COMPANY: {gc_name}
LOCATION: {lead.city}, {lead.county} County, WA

Find the best person at {gc_name} for a subcontractor looking to bid on this project (project manager, estimator, or subcontractor coordinator).

Reply in this EXACT format — one field per line, no extras:
NAME: [first last or blank]
TITLE: [job title or blank]
DEPARTMENT: [department or blank]
EMAIL: [email@domain or blank]
PHONE: [{gc_name} office/direct phone, or blank]

If you are not highly confident about a field, leave it blank.
Do not invent information."""
    else:
        # Public Bid — strict: agency contact only, no GC numbers
        return f"""You are a construction industry researcher helping Scan2Core (a GPR scanning and core drilling company in Washington State) find bid contact information.

PROJECT: {lead.name}
AGENCY/SOURCE: {lead.source}
LOCATION: {lead.city}, {lead.county} County, WA
BID URL: {lead.direct_url or lead.url}

Find the SPECIFIC PROJECT MANAGER or BID COORDINATOR at the AGENCY listed above ({lead.source}).

CRITICAL RULES:
- Return ONLY contact information for the AGENCY/MUNICIPALITY listed above, not for any general contractor.
- Do NOT return any phone number belonging to Sellen Construction, Skanska, Turner, Mortenson, or any other GC.
- If you only know the agency's general main number and not a specific project contact, leave PHONE blank.
- Do NOT guess or hallucinate contact details. Only return information you are highly confident about.

Reply in this EXACT format — one field per line:
NAME: [first last or blank]
TITLE: [job title or blank]
DEPARTMENT: [department or blank]
EMAIL: [email@domain or blank]
PHONE: [direct agency number, or blank]

If you are not confident about a field, leave it blank."""


def parse_ai_response(text: str) -> ContactInfo:
    ci = ContactInfo()
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("NAME:"):
            val = line.split(":", 1)[1].strip()
            if val and val.lower() not in ('blank', 'n/a', 'unknown', ''):
                ci.name = val
        elif line.upper().startswith("TITLE:"):
            val = line.split(":", 1)[1].strip()
            if val and val.lower() not in ('blank', 'n/a', ''):
                ci.title = val
        elif line.upper().startswith("DEPARTMENT:"):
            val = line.split(":", 1)[1].strip()
            if val and val.lower() not in ('blank', 'n/a', ''):
                ci.department = val
        elif line.upper().startswith("EMAIL:"):
            val = line.split(":", 1)[1].strip()
            # Basic email validation
            if val and '@' in val and '.' in val and val.lower() not in ('blank', 'n/a'):
                ci.email = val
        elif line.upper().startswith("PHONE:"):
            val = line.split(":", 1)[1].strip()
            if val and val.lower() not in ('blank', 'n/a', ''):
                # Strip common formatting noise
                val = re.sub(r'[^\d\s\(\)\-\+\.]', '', val).strip()
                digits = re.sub(r'\D', '', val)
                if 7 <= len(digits) <= 11:
                    ci.phone = val
    return ci


def enrich_lead_with_ai(lead: Lead, api_key: str) -> bool:
    """
    Calls Claude Haiku to find contact info for a lead.
    Returns True if any contact info was found.
    """
    prompt = build_enrichment_prompt(lead)
    text = call_claude_api(prompt, api_key)
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
    else:
        lead.enriched = "none"
        return False


# ── PHONE QUALITY ENFORCEMENT ─────────────────────────────────────────────────
def build_gc_phone_set(leads: list[Lead]) -> set[str]:
    """
    Collect all phone numbers from GC Portfolio leads (normalized).
    These are the GC main office lines we don't want appearing on
    public bid leads.
    """
    gc_phones = set()
    for lead in leads:
        if lead.is_gc() and lead.contact_phone:
            normalized = normalize_phone(lead.contact_phone)
            if normalized:
                gc_phones.add(normalized)
    return gc_phones


def build_phone_freq_map(leads: list[Lead]) -> dict[str, int]:
    """Count how many leads each phone number appears on."""
    freq: dict[str, int] = {}
    for lead in leads:
        if lead.contact_phone:
            p = normalize_phone(lead.contact_phone)
            if p:
                freq[p] = freq.get(p, 0) + 1
    return freq


def enforce_phone_quality(leads: list[Lead]) -> int:
    """
    Post-enrichment phone quality pass:
    1. Remove phones from non-GC leads that also appear on any GC lead
       (cross-contamination: AI returned a GC office number for a public bid).
    2. Remove phones from non-GC leads that appear on 5+ leads total
       (frequency heuristic: shared = main line, not project contact).

    Returns number of phones cleared.
    """
    gc_phones = build_gc_phone_set(leads)
    phone_freq = build_phone_freq_map(leads)
    cleared = 0

    for lead in leads:
        if lead.is_gc():
            continue  # Never touch GC lead phone data
        if not lead.contact_phone:
            continue

        p_norm = normalize_phone(lead.contact_phone)

        # Rule 1: phone exists on a GC lead → reject for public bid lead
        if p_norm in gc_phones:
            log(f"  📵 Cleared GC phone from public bid lead: {lead.name[:50]}")
            lead.contact_phone = ""
            # Downgrade enrichment status if phone was the only contact
            if not lead.contact_email and not lead.contact_name:
                lead.enriched = "none"
            elif lead.enriched == "ai":
                lead.enriched = "ai_partial"
            cleared += 1
            continue

        # Rule 2: phone appears on 5+ leads → shared main line
        if phone_freq.get(p_norm, 0) >= SHARED_PHONE_THRESHOLD:
            log(f"  📵 Cleared shared phone ({phone_freq[p_norm]}x) from: {lead.name[:50]}")
            lead.contact_phone = ""
            if not lead.contact_email and not lead.contact_name:
                lead.enriched = "none"
            elif lead.enriched == "ai":
                lead.enriched = "ai_partial"
            cleared += 1

    return cleared


# ── DEDUPLICATION ─────────────────────────────────────────────────────────────
def dedup_leads(new_leads: list[Lead], existing: list[dict]) -> tuple[list[Lead], list[Lead]]:
    """
    Compare incoming scraped leads against existing JSON.
    Returns (to_add, to_skip).
    Dedup key = normalized name + source.
    """
    existing_keys = set()
    for e in existing:
        key = hashlib.md5(f"{e.get('name','')}|{e.get('source','')}".encode()).hexdigest()[:12]
        existing_keys.add(key)

    to_add, to_skip = [], []
    for lead in new_leads:
        if lead.lead_id() in existing_keys:
            to_skip.append(lead)
        else:
            to_add.append(lead)
    return to_add, to_skip


# ── PERSISTENCE ───────────────────────────────────────────────────────────────
def load_existing() -> list[dict]:
    if not os.path.exists(OUTPUT_FILE):
        return []
    try:
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        log(f"Warning: could not read {OUTPUT_FILE}: {e}")
        return []


def save_leads(leads: list[dict]):
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(leads, f, indent=2, ensure_ascii=False)
    log(f"Saved {len(leads)} leads to {OUTPUT_FILE}")


def merge_existing_contact_data(new_lead: Lead, existing: list[dict]) -> bool:
    """
    If an existing lead (same name+source) already has enrichment data,
    copy it onto the new_lead so we don't lose it.
    Returns True if data was copied.
    """
    for e in existing:
        if e.get('name') == new_lead.name and e.get('source') == new_lead.source:
            for field in ['contact_name','contact_title','contact_department',
                          'contact_email','contact_phone','enriched']:
                if e.get(field):
                    setattr(new_lead, field, e[field])
            return True
    return False


# ── BACKFILL ENRICHMENT LOOP ──────────────────────────────────────────────────
def run_backfill(all_leads: list[Lead], api_key: str):
    """
    Process up to BACKFILL_PER_RUN unenriched leads with AI.
    Higher priority leads first.
    """
    unenriched = [l for l in all_leads if l.enriched is None and not l.is_noise_lead()]
    unenriched.sort(key=lambda l: l.priority, reverse=True)

    targets = unenriched[:BACKFILL_PER_RUN]
    log(f"Backfill: {len(targets)} of {len(unenriched)} unenriched leads selected")

    for i, lead in enumerate(targets):
        log(f"  AI {i+1}/{len(targets)}: {lead.name[:60]}")
        success = enrich_lead_with_ai(lead, api_key)
        log(f"  → {'✓ contact found' if success else '✗ no contact'}")
        time.sleep(AI_SLEEP_SECONDS)


# ── MONKEY-PATCH Lead with helper ─────────────────────────────────────────────
def _is_noise_lead(self) -> bool:
    return any(w in self.name.lower() for w in LOW_PRIORITY_KW)
Lead.is_noise_lead = _is_noise_lead


# ── MAIN ──────────────────────────────────────────────────────────────────────
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

    # 4. Restore contact data for any lead we've seen before (re-found on same source)
    for lead in new_leads:
        merge_existing_contact_data(lead, existing)

    # 5. Rebuild full lead list: existing + new (convert existing dicts → Lead objects)
    existing_leads: list[Lead] = []
    for e in existing:
        try:
            lead = Lead(
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
                found_date         = e.get('found_date', ''),
                contact_name       = e.get('contact_name', ''),
                contact_title      = e.get('contact_title', ''),
                contact_department = e.get('contact_department', ''),
                contact_email      = e.get('contact_email', ''),
                contact_phone      = e.get('contact_phone', ''),
                enriched           = e.get('enriched'),
            )
            existing_leads.append(lead)
        except Exception as ex:
            log(f"  skip malformed existing lead: {ex}")

    all_leads = existing_leads + new_leads
    log(f"Total after merge: {len(all_leads)}")

    # 6. AI backfill on unenriched leads
    if api_key:
        log("── AI enrichment ──")
        run_backfill(all_leads, api_key)

    # 7. Phone quality enforcement (post-enrichment pass)
    log("── Phone quality pass ──")
    cleared = enforce_phone_quality(all_leads)
    log(f"Cleared {cleared} suspect phones")

    # 8. Sort by priority desc, then by found_date desc
    all_leads.sort(key=lambda l: (-(l.priority or 0), l.found_date or ''), reverse=False)
    all_leads.sort(key=lambda l: -(l.priority or 0))

    # 9. Save
    save_leads([l.to_dict() for l in all_leads])

    # 10. Summary stats
    total     = len(all_leads)
    has_email = sum(1 for l in all_leads if l.contact_email)
    has_phone = sum(1 for l in all_leads if l.contact_phone)
    gc_leads  = sum(1 for l in all_leads if l.is_gc())
    hot       = sum(1 for l in all_leads if (l.priority or 0) >= 9)
    log(f"── Summary: {total} leads | {hot} hot | {gc_leads} GC | {has_email} email | {has_phone} phone ──")


if __name__ == "__main__":
    main()
