#!/usr/bin/env python3
"""
Scan2Core Daily Lead Hunter v8.0
- All previous scraping intact
- NEW: Deep scrape each lead URL for contact info, close date, description
- NEW: AI enrichment via Claude API + web search for leads where scrape fails
- found_date preserved on re-runs
"""

import os
import json
import requests
import logging
import re
import time
from datetime import datetime
from typing import List, Dict, Optional
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
    'parking', 'garage',
    'data center', 'server room',
    'bridge', 'infrastructure', 'highway', 'road', 'transit', 'light rail', 'station',
    'stadium', 'arena', 'convention',
    'high-rise', 'highrise', 'tower', 'high rise',
    'retrofit', 'seismic', 'renovation', 'remodel', 'modernization',
    'school', 'university', 'college', 'campus', 'educational',
    'office', 'commercial', 'tenant improvement',
    'industrial', 'warehouse', 'manufacturing', 'plant',
    'utility', 'water', 'sewer', 'wastewater', 'treatment plant',
    'port', 'marine', 'dock', 'terminal',
    'concrete', 'structural', 'foundation',
    'facility', 'building', 'construction',
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


class ContactInfo:
    def __init__(self):
        self.name = ''
        self.title = ''
        self.email = ''
        self.phone = ''
        self.department = ''
        self.close_date = ''
        self.bid_number = ''
        self.description = ''
        self.direct_url = ''

    def to_dict(self):
        return {k: v for k, v in {
            'contact_name': self.name,
            'contact_title': self.title,
            'contact_email': self.email,
            'contact_phone': self.phone,
            'contact_department': self.department,
            'close_date': self.close_date,
            'bid_number': self.bid_number,
            'description': self.description,
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
        self.ai_call_limit = 40  # cap per run to control costs

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

    def _is_high_priority(self, text: str) -> bool:
        return any(p in text.lower() for p in HIGH_PRIORITY_TYPES)

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
        if t in SKIP_EXACT:
            return True
        if len(t) < 15:
            return True
        if any(n in t for n in NOISE_PHRASES):
            return True
        if 'http' in t or 'www.' in t:
            return True
        non_ascii = sum(1 for c in text if ord(c) > 127)
        if non_ascii > len(text) * 0.15:
            return True
        return False

    def _score(self, text: str) -> int:
        t = text.lower()
        if any(x in t for x in ['hospital', 'medical', 'healthcare', 'surgery']): return 10
        if any(x in t for x in ['parking', 'garage', 'multifamily', 'apartment', 'housing']): return 9
        if any(x in t for x in ['bridge', 'infrastructure', 'transit', 'light rail']): return 9
        if any(x in t for x in ['university', 'campus', 'school', 'college']): return 8
        if any(x in t for x in ['office', 'commercial', 'warehouse', 'industrial']): return 7
        return 6

    # ── DEEP SCRAPE ───────────────────────────────────────────
    def _scrape_contact_from_page(self, url: str) -> ContactInfo:
        info = ContactInfo()
        if not url or len(url) < 10:
            return info
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code != 200 or not BeautifulSoup:
                return info
            soup = BeautifulSoup(resp.content, 'html.parser')
            text = soup.get_text(separator=' ', strip=True)

            # Email
            emails = re.findall(r'[\w\.-]+@[\w\.-]+\.\w{2,4}', text)
            emails = [e for e in emails if not any(x in e.lower() for x in ['example', 'test', 'noreply', 'no-reply'])]
            if emails:
                info.email = emails[0]

            # Phone
            phones = re.findall(r'(?:\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})', text)
            if phones:
                info.phone = phones[0]

            # Close/due date
            close_patterns = [
                r'(?:due|close[sd]?|deadline|submit(?:tal)?s?\s+due|bid\s+opening|proposals?\s+due)[:\s]+([A-Z][a-z]+\.?\s+\d{1,2},?\s+\d{4})',
                r'(?:due|close[sd]?|deadline)[:\s]+(\d{1,2}/\d{1,2}/\d{2,4})',
                r'(?:due|close[sd]?|deadline)[:\s]+(\w+\s+\d{1,2},?\s+\d{4})',
            ]
            for pat in close_patterns:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    info.close_date = m.group(1).strip()
                    break

            # Bid number
            bid_patterns = [
                r'(?:bid|rfp|rfq|itb|ifb|project|contract)\s*(?:no\.?|number|#)[:\s]*([A-Z0-9][\w\-]{2,20})',
                r'\b(20\d\d-\d{3,4})\b',
                r'\b([A-Z]{2,4}\d{4,6})\b',
            ]
            for pat in bid_patterns:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    info.bid_number = m.group(1).strip()
                    break

            # Contact name + title
            contact_patterns = [
                r'(?:contact|questions?|inquiries?)[:\s]+([A-Z][a-z]+ [A-Z][a-z]+)',
                r'([A-Z][a-z]+ [A-Z][a-z]+)[,\s]+(?:Project Manager|Procurement|Purchasing|Contract|Director|Officer|Coordinator)',
            ]
            for pat in contact_patterns:
                m = re.search(pat, text)
                if m:
                    info.name = m.group(1).strip()
                    break

            # Description — first substantial paragraph
            for tag in soup.find_all(['p', 'div'], limit=30):
                t = tag.get_text(strip=True)
                if len(t) > 80 and self._is_construction(t) and not self._should_skip(t):
                    info.description = t[:400]
                    break

            info.direct_url = url

        except Exception as e:
            logger.debug(f"  Scrape contact error for {url}: {e}")

        return info

    # ── AI ENRICHMENT ─────────────────────────────────────────
    def _ai_enrich(self, lead_name: str, city: str, county: str, source: str, url: str) -> ContactInfo:
        info = ContactInfo()
        if not ANTHROPIC_API_KEY or self.ai_calls >= self.ai_call_limit:
            return info

        self.ai_calls += 1
        logger.info(f"  AI enriching: {lead_name[:60]}")

        prompt = f"""You are helping a concrete scanning and core drilling company (Scan2Core, based in Washington State) find contact information for a construction bid opportunity.

Project: {lead_name}
Agency/City: {city}, {county} County
Source: {source}
URL: {url}

Search the web and find:
1. The procurement contact or project manager for this specific bid (name, title, email, phone)
2. The bid/project close date or deadline
3. The bid number or project number
4. A 1-2 sentence description of the project scope
5. The direct URL to this specific bid (not just the portal homepage)

Return ONLY a JSON object with these exact keys (use empty string if not found):
{{
  "contact_name": "",
  "contact_title": "",
  "contact_email": "",
  "contact_phone": "",
  "contact_department": "",
  "close_date": "",
  "bid_number": "",
  "description": "",
  "direct_url": ""
}}

Return only the JSON, no other text."""

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
                    'max_tokens': 600,
                    'tools': [{'type': 'web_search_20250305', 'name': 'web_search'}],
                    'messages': [{'role': 'user', 'content': prompt}]
                },
                timeout=30
            )

            if resp.status_code != 200:
                logger.warning(f"  AI API error: {resp.status_code}")
                return info

            data = resp.json()
            # Extract text from response
            result_text = ''
            for block in data.get('content', []):
                if block.get('type') == 'text':
                    result_text += block.get('text', '')

            # Parse JSON from response
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

        time.sleep(0.5)  # rate limit buffer
        return info

    # ── ENRICH A LEAD ─────────────────────────────────────────
    def _enrich_lead(self, lead: Dict) -> Dict:
        url = lead.get('url', '')
        name = lead.get('name', '')
        city = lead.get('city', '')
        county = lead.get('county', '')
        source = lead.get('source', '')

        # Skip if already enriched
        if lead.get('contact_email') or lead.get('contact_name') or lead.get('enriched'):
            return lead

        # Step 1: try scraping the page directly
        scraped = self._scrape_contact_from_page(url)

        if scraped.has_useful_info():
            logger.info(f"  Scraped contact for: {name[:50]}")
            lead.update(scraped.to_dict())
            lead['enriched'] = 'scraped'
        else:
            # Step 2: AI enrichment
            ai_info = self._ai_enrich(name, city, county, source, url)
            if ai_info.has_useful_info():
                logger.info(f"  AI found contact for: {name[:50]}")
                lead.update(ai_info.to_dict())
                lead['enriched'] = 'ai'
            else:
                lead['enriched'] = 'none'

        return lead

    # ── ADD LEAD ──────────────────────────────────────────────
    def _add_lead(self, leads: List, name: str, city: str, county: str,
                  source: str, url: str = '', gc: str = '', notes: str = '', text: str = ''):
        name = name.strip()[:150]
        if len(name) < 15:
            return
        lead_id = f"{name}|{source}"
        if lead_id in self.found:
            return
        lead = {
            'name': name,
            'city': city,
            'county': county,
            'status': 'Posted',
            'type': 'GC Project' if gc else 'Public Bid',
            'gc': gc,
            'source': source,
            'url': url,
            'notes': notes or 'Review specs to confirm scanning + core drilling needed',
            'priority': self._score(text or name),
            'found_date': datetime.now().isoformat(),
            'enriched': None,
        }
        leads.append(lead)
        self.found[lead_id] = lead
        logger.info(f"  FOUND [{source}]: {name[:70]}")

    # ── SCRAPERS ──────────────────────────────────────────────
    def scrape_wordpress_bids(self, source_name: str, url: str, city: str, county: str) -> List[Dict]:
        leads = []
        try:
            resp = self.session.get(url, timeout=20)
            logger.info(f"  {source_name}: HTTP {resp.status_code} ({len(resp.content)} bytes)")
            if resp.status_code != 200 or not BeautifulSoup:
                return leads
            soup = BeautifulSoup(resp.content, 'html.parser')
            for tag in soup.find_all(['nav', 'footer', 'header', 'script', 'style']):
                tag.decompose()
            for a in soup.find_all('a', href=True):
                href = a.get('href', '')
                text = a.get_text(strip=True)
                if not text or len(text) < 20 or len(text) > 250:
                    continue
                if self._should_skip(text) or self._is_closed(text):
                    continue
                url_years = re.findall(r'/(\d{4})/', href)
                if url_years and CURRENT_YEAR not in url_years:
                    continue
                if not self._is_construction(text):
                    continue
                self._add_lead(leads, text, city, county, source_name, href, text=text)
        except Exception as e:
            logger.warning(f"  {source_name} error: {e}")
        logger.info(f"  {source_name}: {len(leads)} leads found")
        return leads

    def scrape_city_bid_page(self, city: str, county: str, url: str) -> List[Dict]:
        leads = []
        try:
            resp = self.session.get(url, timeout=20)
            if resp.status_code != 200 or not BeautifulSoup:
                logger.debug(f"  {city}: HTTP {resp.status_code}")
                return leads
            soup = BeautifulSoup(resp.content, 'html.parser')
            for tag in soup.find_all(['nav', 'footer', 'header', 'script', 'style']):
                tag.decompose()
            seen_texts = set()
            for el in soup.find_all(['h2', 'h3', 'h4', 'a', 'li', 'td']):
                text = el.get_text(separator=' ', strip=True)
                text = re.sub(r'\s+', ' ', text).strip()
                if text in seen_texts:
                    continue
                seen_texts.add(text)
                if self._should_skip(text) or self._is_closed(text):
                    continue
                if len(text) > 250:
                    continue
                if not self._is_current_year(text):
                    continue
                if not self._is_construction(text):
                    continue
                href = el.get('href', '') if el.name == 'a' else ''
                if not href:
                    link = el.find('a', href=True)
                    href = link.get('href', '') if link else url
                full_url = href if href.startswith('http') else urljoin(url, href)
                self._add_lead(leads, text, city, county,
                               f'City Bids - {city}', full_url, text=text)
        except Exception as e:
            logger.debug(f"  {city} error: {e}")
        return leads

    def scrape_all_city_pages(self) -> List[Dict]:
        leads = []
        logger.info(f"Scraping {len(WA_CITY_BID_PAGES)} WA city/county bid pages...")
        for city, county, url in WA_CITY_BID_PAGES:
            city_leads = self.scrape_city_bid_page(city, county, url)
            if city_leads:
                logger.info(f"  {city}: {len(city_leads)} leads")
            leads.extend(city_leads)
        logger.info(f"  City pages total: {len(leads)} leads found")
        return leads

    # ── SAVE ──────────────────────────────────────────────────
    def save_results(self, all_leads: List[Dict]):
        existing = []
        existing_by_name = {}
        if os.path.exists(FOUND_FILE):
            try:
                with open(FOUND_FILE, 'r') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    existing = data
                elif isinstance(data, dict):
                    existing = [v for v in data.values() if isinstance(v, dict) and 'name' in v]
            except:
                existing = []

        for e in existing:
            existing_by_name[e.get('name', '')] = e

        new_leads = []
        for lead in all_leads:
            if lead.get('name', '') not in existing_by_name:
                new_leads.append(lead)

        combined = new_leads + existing
        with open(FOUND_FILE, 'w') as f:
            json.dump(combined, f, indent=2)

        logger.info(f"Saved {len(new_leads)} new leads ({len(combined)} total in file)")
        return new_leads

    # ── RUN ───────────────────────────────────────────────────
    def run(self):
        logger.info("=" * 60)
        logger.info("Scan2Core Daily Lead Hunter v8.0 starting...")
        logger.info(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        logger.info(f"AI enrichment: {'enabled' if ANTHROPIC_API_KEY else 'disabled (no API key)'}")
        logger.info("=" * 60)

        # Step 1: scrape all sources
        all_leads = []
        all_leads.extend(self.scrape_wordpress_bids(
            'Seattle Buy Line',
            'https://thebuyline.seattle.gov/category/bids-and-proposals/',
            'Seattle', 'King'))
        all_leads.extend(self.scrape_wordpress_bids(
            'Seattle Consultant Connection',
            'https://consultants.seattle.gov/category/bids-proposals/',
            'Seattle', 'King'))
        all_leads.extend(self.scrape_all_city_pages())

        # Step 2: enrich only new leads
        new_leads = self.save_results(all_leads)

        if new_leads:
            logger.info(f"Enriching {len(new_leads)} new leads...")
            enriched_leads = []
            for lead in new_leads:
                enriched = self._enrich_lead(lead)
                enriched_leads.append(enriched)

            # Re-save with enrichment data
            existing = []
            if os.path.exists(FOUND_FILE):
                try:
                    with open(FOUND_FILE, 'r') as f:
                        existing = json.load(f)
                    if not isinstance(existing, list):
                        existing = []
                except:
                    existing = []

            # Replace the new leads at the front with enriched versions
            enriched_names = {l.get('name', '') for l in enriched_leads}
            old_leads = [l for l in existing if l.get('name', '') not in enriched_names]
            combined = enriched_leads + old_leads

            with open(FOUND_FILE, 'w') as f:
                json.dump(combined, f, indent=2)

            logger.info(f"AI calls used: {self.ai_calls}/{self.ai_call_limit}")

        logger.info("=" * 60)
        logger.info(f"DONE. New leads today: {len(new_leads)}")
        if new_leads:
            logger.info("NEW LEADS:")
            for l in sorted(new_leads, key=lambda x: x.get('priority', 0), reverse=True):
                contact = l.get('contact_email') or l.get('contact_name') or 'no contact yet'
                logger.info(f"  [{l['priority']}] {l['name'][:55]} | {contact}")
        else:
            logger.info("No new leads found today.")
        logger.info("=" * 60)


if __name__ == '__main__':
    bot = Scan2CoreBot()
    bot.run()
