#!/usr/bin/env python3
"""
Scan2Core Daily Lead Hunter v8.1
- Scrapes 37 WA city/county bid pages daily
- AI enrichment on NEW leads immediately
- Also enriches 40 existing unenriched leads per run (backfill)
- found_date preserved on re-runs
"""

import os
import json
import requests
import logging
import re
import time
from datetime import datetime
from typing import List, Dict
from urllib.parse import urljoin

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

WORKSPACE = os.getenv('GITHUB_WORKSPACE', '/tmp')
FOUND_FILE = os.path.join(WORKSPACE, 'found_projects.json')
CURRENT_YEAR = str(datetime.now().year)
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')

HIGH_PRIORITY_TYPES = [
    'hospital', 'medical', 'clinic', 'healthcare', 'surgery', 'health',
    'apartment', 'residential', 'multifamily', 'multi-family', 'housing', 'mixed-use',
    'parking', 'garage', 'data center', 'server room',
    'bridge', 'infrastructure', 'highway', 'road', 'transit', 'light rail', 'station',
    'stadium', 'arena', 'convention', 'high-rise', 'highrise', 'tower', 'high rise',
    'retrofit', 'seismic', 'renovation', 'remodel', 'modernization',
    'school', 'university', 'college', 'campus', 'educational',
    'office', 'commercial', 'tenant improvement',
    'industrial', 'warehouse', 'manufacturing', 'plant',
    'utility', 'water', 'sewer', 'wastewater', 'treatment plant',
    'port', 'marine', 'dock', 'terminal',
    'concrete', 'structural', 'foundation', 'facility', 'building', 'construction',
]

CONSTRUCTION_KEYWORDS = [
    'bid', 'rfq', 'rfp', 'ifb', 'construct', 'project', 'build', 'renovation',
    'contract', 'install', 'repair', 'upgrade', 'facility', 'phase',
    'structural', 'concrete', 'foundation', 'infrastructure', 'work',
    'demolish', 'improvement', 'modernization', 'replacement', 'solicitation',
    'engineering', 'design', 'services', 'maintenance',
]

SKIP_EXACT = {
    'home', 'contact', 'about', 'login', 'search', 'menu',
    'next', 'previous', 'submit', 'more', 'back', 'top',
    'facebook', 'twitter', 'linkedin', 'instagram', 'youtube',
    'subscribe', 'newsletter', 'sitemap', 'privacy', 'register',
    'bids', 'rfps', 'rfqs', 'procurement', 'purchasing',
}

NOISE_PHRASES = [
    'click here', 'access the', 'please post', 'post the information',
    'view the', 'read more', 'learn more', 'sign up', 'register now',
    'follow us', 'contact us', 'get more info', 'no bids', 'no current',
    'no open', 'there are no', 'return to', 'back to', 'go to', 'see all',
    'legal ad', 'plan holders', 'addendum', 'tabulation', 'notice of intent',
    'plan set', 'bid drawings', 'contract documents', 'scope of work',
    'exhibit a', 'exhibit b', 'exhibit c', 'attachment a', 'attachment b',
    'sign in sheet', 'pre-bid sign', 'contacts list', 'doing business with',
    'join the meeting', 'microsoft teams', 'how does the city',
    'city hall closure', 'employee services', 'future competitive',
    'certifying your company', 'who should i contact',
    'finance and administrative', 'general services', 'professional services',
    'goods and services', 'goods, services',
]

CLOSED_PREFIXES = ('closed', 'archived', 'canceled', 'cancelled', 'awarded', 'closed*')

WA_CITY_BID_PAGES = [
    ('Auburn', 'King', 'https://www.auburnwa.gov/city_hall/documents/request_for_bids_proposals'),
    ('Bellevue', 'King', 'https://bellevuewa.gov/city-government/departments/finance/bid-opportunities-rfps-and-rfqs'),
    ('Bothell', 'King', 'http://www.ci.bothell.wa.us/bids.aspx'),
    ('Burien', 'King', 'https://www.burienwa.gov/city_hall/working_with_us/bids_rfp_rfq'),
    ('Federal Way', 'King', 'https://www.cityoffederalway.com/bids'),
    ('Issaquah', 'King', 'https://issaquahwa.gov/1464/Bids-RFPs'),
    ('Kent', 'King', 'https://www.kentwa.gov/pay-and-apply/bids-procurement-rfps'),
    ('King County', 'King', 'https://kingcounty.gov/depts/finance-business-operations/procurement.aspx'),
    ('Kirkland', 'King', 'https://www.kirklandwa.gov/Government/Departments/Finance-and-Administration/Purchasing-Services/Doing-Business-with-the-City'),
    ('Mercer Island', 'King', 'https://www.mercerisland.gov/rfps'),
    ('Redmond', 'King', 'https://www.redmond.gov/445/Bidding-Contracting'),
    ('Renton', 'King', 'https://www.rentonwa.gov/city_hall/executive_services/city_clerk/CallForBids'),
    ('SeaTac', 'King', 'https://www.seatacwa.gov/business/rfp-rfq-bid-procurement'),
    ('Seattle', 'King', 'https://www.seattle.gov/purchasing-and-contracting/construction-contracting'),
    ('Seattle Public Schools', 'King', 'https://www.seattleschools.org/departments/finance/procurement/current-solicitations/'),
    ('Shoreline', 'King', 'https://www.shorelinewa.gov/government/departments/administrative-services/bids-rfps'),
    ('Port of Seattle', 'King', 'https://www.portseattle.org/business/bid-opportunities'),
    ('Bellevue School District', 'King', 'https://www.bsd405.org/about-us/departments/finance/open-bids'),
    ('Everett', 'Snohomish', 'https://www.everettwa.gov/319/Procurement'),
    ('Lynnwood', 'Snohomish', 'https://www.lynnwoodwa.gov/Government/City-Clerk/Procurement-Contracts-Division/Current-Contract-Opportunities'),
    ('Marysville', 'Snohomish', 'https://marysvillewa.gov/Bids.aspx'),
    ('Monroe', 'Snohomish', 'https://www.monroewa.gov/bids.aspx'),
    ('Snohomish County', 'Snohomish', 'https://snohomishcountywa.gov/3706/Purchasing-Portal'),
    ('Lakewood', 'Pierce', 'https://cityoflakewood.us/category/rfp-rfq-bids/'),
    ('Pierce County', 'Pierce', 'https://www.piercecountywa.gov/5260/Current-Solicitations'),
    ('Puyallup', 'Pierce', 'https://www.cityofpuyallup.org/Bids.aspx'),
    ('Tacoma', 'Pierce', 'https://www.cityoftacoma.org/government/city_departments/finance/procurement_and_payables_division/purchasing/contracting_opportunities'),
    ('Tacoma Public Schools', 'Pierce', 'https://www.tacomaschools.org/departments/purchasing'),
    ('University Place', 'Pierce', 'https://cityofup.com/Bids.aspx'),
    ('Lacey', 'Thurston', 'http://www.ci.lacey.wa.us/city-government/city-departments/public-works/solicitations'),
    ('Olympia', 'Thurston', 'https://www.olympiawa.gov/government/contracts___purchasing/bids.php'),
    ('Thurston County', 'Thurston', 'https://www.thurstoncountywa.gov/cs/Pages/bids-projects.aspx'),
    ('Bremerton', 'Kitsap', 'https://bremertonwa.gov/bids.aspx'),
    ('Kitsap County PW', 'Kitsap', 'https://www.kitsap.gov/pw/Pages/Current-Requests-For-Proposals-.aspx'),
    ('WA Dept of Enterprise Services', 'State', 'https://des.wa.gov/services/contracting-purchasing/doing-business-state/bid-opportunities'),
    ('University of Washington', 'King', 'https://facilities.uw.edu/projects/business-opportunities/solicitations'),
    ('Sound Transit', 'Multi', 'https://www.soundtransit.org/doing-business-sound-transit/selling-sound-transit/solicitations'),
]

# How many existing leads to enrich per run (controls API cost)
BACKFILL_PER_RUN = 40


class ContactInfo:
    def __init__(self):
        self.name = ''; self.title = ''; self.email = ''; self.phone = ''
        self.department = ''; self.close_date = ''; self.bid_number = ''
        self.description = ''; self.direct_url = ''

    def to_dict(self):
        return {k: v for k, v in {
            'contact_name': self.name, 'contact_title': self.title,
            'contact_email': self.email, 'contact_phone': self.phone,
            'contact_department': self.department, 'close_date': self.close_date,
            'bid_number': self.bid_number, 'description': self.description,
            'direct_url': self.direct_url,
        }.items() if v}

    def has_useful_info(self):
        return any([self.email, self.phone, self.name, self.close_date, self.description])


class Scan2CoreBot:
    def __init__(self):
        self.found = self._load_found()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        self.ai_calls = 0
        self.ai_call_limit = 50

    def _load_found(self) -> Dict:
        if os.path.exists(FOUND_FILE):
            try:
                with open(FOUND_FILE, 'r') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return {item.get('name', '') + '|' + item.get('source', ''): item
                            for item in data if isinstance(item, dict)}
                return {k: v for k, v in data.items() if isinstance(v, dict)}
            except:
                return {}
        return {}

    def _is_construction(self, text: str) -> bool:
        return any(k in text.lower() for k in CONSTRUCTION_KEYWORDS)

    def _is_current_year(self, text: str) -> bool:
        years_found = re.findall(r'20\d\d', text)
        if not years_found:
            return True
        return CURRENT_YEAR in years_found

    def _is_closed(self, text: str) -> bool:
        return any(text.lower().strip().startswith(p) for p in CLOSED_PREFIXES)

    def _should_skip(self, text: str) -> bool:
        t = text.lower().strip()
        if t in SKIP_EXACT: return True
        if len(t) < 15: return True
        if any(n in t for n in NOISE_PHRASES): return True
        if 'http' in t or 'www.' in t: return True
        non_ascii = sum(1 for c in text if ord(c) > 127)
        if non_ascii > len(text) * 0.15: return True
        return False

    def _score(self, text: str) -> int:
        t = text.lower()
        if any(x in t for x in ['hospital', 'medical', 'healthcare', 'surgery']): return 10
        if any(x in t for x in ['parking', 'garage', 'multifamily', 'apartment', 'housing']): return 9
        if any(x in t for x in ['bridge', 'infrastructure', 'transit', 'light rail']): return 9
        if any(x in t for x in ['university', 'campus', 'school', 'college']): return 8
        if any(x in t for x in ['office', 'commercial', 'warehouse', 'industrial']): return 7
        return 6

    def _scrape_contact_from_page(self, url: str) -> ContactInfo:
        info = ContactInfo()
        if not url or len(url) < 10: return info
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code != 200 or not BeautifulSoup: return info
            soup = BeautifulSoup(resp.content, 'html.parser')
            text = soup.get_text(separator=' ', strip=True)

            emails = re.findall(r'[\w\.-]+@[\w\.-]+\.\w{2,4}', text)
            emails = [e for e in emails if not any(x in e.lower() for x in ['example', 'test', 'noreply', 'no-reply'])]
            if emails: info.email = emails[0]

            phones = re.findall(r'(?:\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})', text)
            if phones: info.phone = phones[0]

            for pat in [
                r'(?:due|close[sd]?|deadline|submit(?:tal)?s?\s+due|bid\s+opening)[:\s]+([A-Z][a-z]+\.?\s+\d{1,2},?\s+\d{4})',
                r'(?:due|close[sd]?|deadline)[:\s]+(\d{1,2}/\d{1,2}/\d{2,4})',
                r'(?:due|close[sd]?|deadline)[:\s]+(\w+\s+\d{1,2},?\s+\d{4})',
            ]:
                m = re.search(pat, text, re.IGNORECASE)
                if m: info.close_date = m.group(1).strip(); break

            for pat in [
                r'(?:bid|rfp|rfq|itb|ifb|project|contract)\s*(?:no\.?|number|#)[:\s]*([A-Z0-9][\w\-]{2,20})',
                r'\b(20\d\d-\d{3,4})\b',
                r'\b([A-Z]{2,4}\d{4,6})\b',
            ]:
                m = re.search(pat, text, re.IGNORECASE)
                if m: info.bid_number = m.group(1).strip(); break

            for pat in [
                r'(?:contact|questions?|inquiries?)[:\s]+([A-Z][a-z]+ [A-Z][a-z]+)',
                r'([A-Z][a-z]+ [A-Z][a-z]+)[,\s]+(?:Project Manager|Procurement|Purchasing|Contract|Director|Officer|Coordinator)',
            ]:
                m = re.search(pat, text)
                if m: info.name = m.group(1).strip(); break

            for tag in soup.find_all(['p', 'div'], limit=30):
                t = tag.get_text(strip=True)
                if len(t) > 80 and self._is_construction(t) and not self._should_skip(t):
                    info.description = t[:400]; break

            info.direct_url = url
        except Exception as e:
            logger.debug(f"  Scrape error {url}: {e}")
        return info

    def _ai_enrich(self, lead_name: str, city: str, county: str, source: str, url: str) -> ContactInfo:
        info = ContactInfo()
        if not ANTHROPIC_API_KEY or self.ai_calls >= self.ai_call_limit:
            return info

        self.ai_calls += 1
        logger.info(f"  AI enriching ({self.ai_calls}/{self.ai_call_limit}): {lead_name[:55]}")

        prompt = f"""Find contact info for this WA State construction bid. Scan2Core (GPR scanning + core drilling company) needs to reach the procurement officer or project manager.

Project: {lead_name}
Agency: {city}, {county} County
Source: {source}
URL: {url}

Search the web. Return ONLY this JSON (empty string if not found):
{{"contact_name":"","contact_title":"","contact_email":"","contact_phone":"","contact_department":"","close_date":"","bid_number":"","description":"","direct_url":""}}"""

        try:
            resp = requests.post(
                'https://api.anthropic.com/v1/messages',
                headers={
                    'x-api-key': ANTHROPIC_API_KEY,
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json',
                },
                json={
                    'model': 'claude-haiku-4-5-20251001',
                    'max_tokens': 500,
                    'tools': [{'type': 'web_search_20250305', 'name': 'web_search'}],
                    'messages': [{'role': 'user', 'content': prompt}]
                },
                timeout=30
            )

            if resp.status_code != 200:
                logger.warning(f"  AI API error: {resp.status_code} — {resp.text[:200]}")
                return info

            data = resp.json()
            result_text = ''.join(b.get('text', '') for b in data.get('content', []) if b.get('type') == 'text')

            json_match = re.search(r'\{[^{}]+\}', result_text, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                info.name = parsed.get('contact_name', '')
                info.title = parsed.get('contact_title', '')
                info.email = parsed.get('contact_email', '')
                info.phone = parsed.get('contact_phone', '')
                info.department = parsed.get('contact_department', '')
                info.close_date = parsed.get('close_date', '')
                info.bid_number = parsed.get('bid_number', '')
                info.description = parsed.get('description', '')
                info.direct_url = parsed.get('direct_url', '') or url

        except Exception as e:
            logger.warning(f"  AI enrich error: {e}")

        time.sleep(0.4)
        return info

    def _enrich_lead(self, lead: Dict) -> Dict:
        """Try scrape first, then AI. Skip if already has useful data."""
        # Already has contact info — skip
        if lead.get('contact_email') or lead.get('contact_name') or lead.get('contact_phone'):
            return lead
        # Marked as 'none' previously and it's been tried — skip
        if lead.get('enriched') in ('scraped', 'ai'):
            return lead

        url = lead.get('url', '')
        scraped = self._scrape_contact_from_page(url)

        if scraped.has_useful_info():
            lead.update(scraped.to_dict())
            lead['enriched'] = 'scraped'
            logger.info(f"  ✓ Scraped: {lead.get('name','')[:50]}")
        else:
            ai_info = self._ai_enrich(
                lead.get('name', ''), lead.get('city', ''),
                lead.get('county', ''), lead.get('source', ''), url
            )
            if ai_info.has_useful_info():
                lead.update(ai_info.to_dict())
                lead['enriched'] = 'ai'
                logger.info(f"  ✓ AI found: {lead.get('name','')[:50]}")
            else:
                lead['enriched'] = 'none'

        return lead

    def _add_lead(self, leads: List, name: str, city: str, county: str,
                  source: str, url: str = '', gc: str = '', notes: str = '', text: str = ''):
        name = name.strip()[:150]
        if len(name) < 15: return
        lead_id = f"{name}|{source}"
        if lead_id in self.found: return
        lead = {
            'name': name, 'city': city, 'county': county,
            'status': 'Posted', 'type': 'GC Project' if gc else 'Public Bid',
            'gc': gc, 'source': source, 'url': url,
            'notes': notes or 'Review specs to confirm scanning + core drilling needed',
            'priority': self._score(text or name),
            'found_date': datetime.now().isoformat(),
            'enriched': None,
        }
        leads.append(lead)
        self.found[lead_id] = lead
        logger.info(f"  FOUND [{source}]: {name[:70]}")

    def scrape_wordpress_bids(self, source_name: str, url: str, city: str, county: str) -> List[Dict]:
        leads = []
        try:
            resp = self.session.get(url, timeout=20)
            logger.info(f"  {source_name}: HTTP {resp.status_code}")
            if resp.status_code != 200 or not BeautifulSoup: return leads
            soup = BeautifulSoup(resp.content, 'html.parser')
            for tag in soup.find_all(['nav', 'footer', 'header', 'script', 'style']): tag.decompose()
            for a in soup.find_all('a', href=True):
                href = a.get('href', ''); text = a.get_text(strip=True)
                if not text or len(text) < 20 or len(text) > 250: continue
                if self._should_skip(text) or self._is_closed(text): continue
                url_years = re.findall(r'/(\d{4})/', href)
                if url_years and CURRENT_YEAR not in url_years: continue
                if not self._is_construction(text): continue
                self._add_lead(leads, text, city, county, source_name, href, text=text)
        except Exception as e:
            logger.warning(f"  {source_name} error: {e}")
        logger.info(f"  {source_name}: {len(leads)} leads found")
        return leads

    def scrape_city_bid_page(self, city: str, county: str, url: str) -> List[Dict]:
        leads = []
        try:
            resp = self.session.get(url, timeout=20)
            if resp.status_code != 200 or not BeautifulSoup: return leads
            soup = BeautifulSoup(resp.content, 'html.parser')
            for tag in soup.find_all(['nav', 'footer', 'header', 'script', 'style']): tag.decompose()
            seen_texts = set()
            for el in soup.find_all(['h2', 'h3', 'h4', 'a', 'li', 'td']):
                text = re.sub(r'\s+', ' ', el.get_text(separator=' ', strip=True)).strip()
                if text in seen_texts: continue
                seen_texts.add(text)
                if self._should_skip(text) or self._is_closed(text): continue
                if len(text) > 250: continue
                if not self._is_current_year(text): continue
                if not self._is_construction(text): continue
                href = el.get('href', '') if el.name == 'a' else ''
                if not href:
                    link = el.find('a', href=True)
                    href = link.get('href', '') if link else url
                full_url = href if href.startswith('http') else urljoin(url, href)
                self._add_lead(leads, text, city, county, f'City Bids - {city}', full_url, text=text)
        except Exception as e:
            logger.debug(f"  {city} error: {e}")
        return leads

    def scrape_all_city_pages(self) -> List[Dict]:
        leads = []
        logger.info(f"Scraping {len(WA_CITY_BID_PAGES)} WA city/county bid pages...")
        for city, county, url in WA_CITY_BID_PAGES:
            city_leads = self.scrape_city_bid_page(city, county, url)
            if city_leads: logger.info(f"  {city}: {len(city_leads)} leads")
            leads.extend(city_leads)
        logger.info(f"  City pages total: {len(leads)} leads found")
        return leads

    def load_all_leads(self) -> List[Dict]:
        if os.path.exists(FOUND_FILE):
            try:
                with open(FOUND_FILE, 'r') as f:
                    data = json.load(f)
                return data if isinstance(data, list) else []
            except:
                return []
        return []

    def save_all_leads(self, leads: List[Dict]):
        with open(FOUND_FILE, 'w') as f:
            json.dump(leads, f, indent=2)

    def run(self):
        logger.info("=" * 60)
        logger.info("Scan2Core Daily Lead Hunter v8.1 starting...")
        logger.info(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        logger.info(f"AI enrichment: {'ENABLED' if ANTHROPIC_API_KEY else 'DISABLED (no API key)'}")
        logger.info("=" * 60)

        # ── STEP 1: Scrape for new leads ──────────────────────
        all_scraped = []
        all_scraped.extend(self.scrape_wordpress_bids(
            'Seattle Buy Line', 'https://thebuyline.seattle.gov/category/bids-and-proposals/', 'Seattle', 'King'))
        all_scraped.extend(self.scrape_wordpress_bids(
            'Seattle Consultant Connection', 'https://consultants.seattle.gov/category/bids-proposals/', 'Seattle', 'King'))
        all_scraped.extend(self.scrape_all_city_pages())

        # ── STEP 2: Load existing, find what's new ────────────
        existing = self.load_all_leads()
        existing_names = {l.get('name', '') for l in existing}
        new_leads = [l for l in all_scraped if l.get('name', '') not in existing_names]
        logger.info(f"New leads found today: {len(new_leads)}")

        # ── STEP 3: Enrich new leads immediately ──────────────
        if new_leads and ANTHROPIC_API_KEY:
            logger.info(f"Enriching {len(new_leads)} new leads...")
            new_leads = [self._enrich_lead(l) for l in new_leads]

        # ── STEP 4: Backfill existing unenriched leads ────────
        # Pick up leads that have never been enriched (enriched=None or enriched='none')
        # sorted by priority so highest-value leads get contact info first
        if ANTHROPIC_API_KEY and self.ai_calls < self.ai_call_limit:
            unenriched = [
                l for l in existing
                if not l.get('contact_email')
                and not l.get('contact_name')
                and not l.get('contact_phone')
                and l.get('enriched') not in ('scraped', 'ai')
            ]
            # Sort by priority desc so we do best leads first
            unenriched.sort(key=lambda x: x.get('priority', 0), reverse=True)
            backfill_batch = unenriched[:BACKFILL_PER_RUN]

            if backfill_batch:
                logger.info(f"Backfill: enriching {len(backfill_batch)} existing leads (priority 9+ first)...")
                backfill_names = {l.get('name', '') for l in backfill_batch}
                for lead in backfill_batch:
                    self._enrich_lead(lead)
                    if self.ai_calls >= self.ai_call_limit:
                        break
                logger.info(f"Backfill complete. {len([l for l in backfill_batch if l.get('contact_email') or l.get('contact_name')])} contacts found.")
            else:
                logger.info("Backfill: all existing leads already processed.")

        # ── STEP 5: Save everything ───────────────────────────
        # New leads go to front, existing (now potentially updated) behind
        combined = new_leads + existing
        self.save_all_leads(combined)

        total_with_contacts = sum(1 for l in combined if l.get('contact_email') or l.get('contact_name') or l.get('contact_phone'))
        remaining_unenriched = sum(1 for l in combined if not l.get('contact_email') and not l.get('contact_name') and l.get('enriched') not in ('scraped', 'ai'))

        logger.info("=" * 60)
        logger.info(f"DONE.")
        logger.info(f"  New leads today: {len(new_leads)}")
        logger.info(f"  Total leads: {len(combined)}")
        logger.info(f"  With contacts: {total_with_contacts}")
        logger.info(f"  Still unenriched: {remaining_unenriched} (clears in ~{remaining_unenriched // BACKFILL_PER_RUN + 1} more runs)")
        logger.info(f"  AI calls used: {self.ai_calls}/{self.ai_call_limit}")
        if new_leads:
            logger.info("NEW LEADS:")
            for l in sorted(new_leads, key=lambda x: x.get('priority', 0), reverse=True):
                contact = l.get('contact_email') or l.get('contact_name') or 'pending enrichment'
                logger.info(f"  [{l['priority']}] {l['name'][:55]} | {contact}")
        logger.info("=" * 60)


if __name__ == '__main__':
    bot = Scan2CoreBot()
    bot.run()
