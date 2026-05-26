#!/usr/bin/env python3
"""
Scan2Core Daily Lead Hunter v7.4
- Removed broken Bonfire scraper (JS-rendered, DNS fails in Actions)
- Seattle covered by city pages list
- BuyLine/Consultants: skip CLOSED/ARCHIVED/CANCELED, filter by URL year
- GC pages removed (JS-rendered)
- found_date preserved on re-runs (never overwritten)
"""

import os
import json
import requests
import logging
import re
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
    # King County
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
    # Snohomish County
    ('Everett', 'Snohomish', 'https://www.everettwa.gov/319/Procurement'),
    ('Lynnwood', 'Snohomish', 'https://www.lynnwoodwa.gov/Government/City-Clerk/Procurement-Contracts-Division/Current-Contract-Opportunities'),
    ('Marysville', 'Snohomish', 'https://marysvillewa.gov/Bids.aspx'),
    ('Monroe', 'Snohomish', 'https://www.monroewa.gov/bids.aspx'),
    ('Snohomish County', 'Snohomish', 'https://snohomishcountywa.gov/3706/Purchasing-Portal'),
    # Pierce County
    ('Lakewood', 'Pierce', 'https://cityoflakewood.us/category/rfp-rfq-bids/'),
    ('Pierce County', 'Pierce', 'https://www.piercecountywa.gov/5260/Current-Solicitations'),
    ('Puyallup', 'Pierce', 'https://www.cityofpuyallup.org/Bids.aspx'),
    ('Tacoma', 'Pierce', 'https://www.cityoftacoma.org/government/city_departments/finance/procurement_and_payables_division/purchasing/contracting_opportunities'),
    ('Tacoma Public Schools', 'Pierce', 'https://www.tacomaschools.org/departments/purchasing'),
    ('University Place', 'Pierce', 'https://cityofup.com/Bids.aspx'),
    # Thurston County
    ('Lacey', 'Thurston', 'http://www.ci.lacey.wa.us/city-government/city-departments/public-works/solicitations'),
    ('Olympia', 'Thurston', 'https://www.olympiawa.gov/government/contracts___purchasing/bids.php'),
    ('Thurston County', 'Thurston', 'https://www.thurstoncountywa.gov/cs/Pages/bids-projects.aspx'),
    # Kitsap County
    ('Bremerton', 'Kitsap', 'https://bremertonwa.gov/bids.aspx'),
    ('Kitsap County PW', 'Kitsap', 'https://www.kitsap.gov/pw/Pages/Current-Requests-For-Proposals-.aspx'),
    # State/regional
    ('WA Dept of Enterprise Services', 'State', 'https://des.wa.gov/services/contracting-purchasing/doing-business-state/bid-opportunities'),
    ('University of Washington', 'King', 'https://facilities.uw.edu/projects/business-opportunities/solicitations'),
    ('Sound Transit', 'Multi', 'https://www.soundtransit.org/doing-business-sound-transit/selling-sound-transit/solicitations'),
]


class Scan2CoreBot:
    def __init__(self):
        self.found = self._load_found()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        })

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
            'found_date': datetime.now().isoformat()
        }
        leads.append(lead)
        self.found[lead_id] = lead
        logger.info(f"  FOUND [{source}]: {name[:70]}")

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

        # Index existing entries by name to preserve original found_date
        for e in existing:
            existing_by_name[e.get('name', '')] = e

        # Only add leads that don't already exist — never overwrite found_date
        new_leads = []
        for lead in all_leads:
            name = lead.get('name', '')
            if name in existing_by_name:
                continue
            new_leads.append(lead)

        combined = new_leads + existing

        with open(FOUND_FILE, 'w') as f:
            json.dump(combined, f, indent=2)

        logger.info(f"Saved {len(new_leads)} new leads ({len(combined)} total in file)")
        return new_leads

    def run(self):
        logger.info("=" * 60)
        logger.info("Scan2Core Daily Lead Hunter v7.4 starting...")
        logger.info(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        logger.info("=" * 60)

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

        new_leads = self.save_results(all_leads)

        logger.info("=" * 60)
        logger.info(f"DONE. New leads today: {len(new_leads)}")
        if new_leads:
            logger.info("NEW LEADS:")
            for l in sorted(new_leads, key=lambda x: x.get('priority', 0), reverse=True):
                logger.info(f"  [{l['priority']}] {l['name'][:60]} | {l['source']}")
        else:
            logger.info("No new leads found today.")
        logger.info("=" * 60)


if __name__ == '__main__':
    bot = Scan2CoreBot()
    bot.run()
