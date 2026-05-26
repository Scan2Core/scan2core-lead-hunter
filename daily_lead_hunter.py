#!/usr/bin/env python3
"""
Scan2Core Daily Lead Hunter v7.1
Direct scraping of verified WA city/county bid listing pages.
Filters to current year bids only to avoid archived results.
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
PREV_YEAR = str(datetime.now().year - 1)

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
    'sign in sheet', 'pre-bid sign', 'contacts list',
]

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
    ('Seattle', 'King', 'https://www.seattle.gov/purchasing-and-contracting/purchasing'),
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
    ('Kitsap County', 'Kitsap', 'https://www.kitsapgov.com/das/Pages/Online-Bids.aspx'),
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
        """Returns True if text contains current year, prev year, or no year at all."""
        years_found = re.findall(r'20\d\d', text)
        if not years_found:
            return True  # no year = probably fine
        return any(y in (CURRENT_YEAR, PREV_YEAR) for y in years_found)

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

    def scrape_public_bid_tracker(self) -> List[Dict]:
        leads = []
        logger.info("Scraping publicbidtracker.com (WA state WEBS bids)...")
        try:
            self.session.get('https://publicbidtracker.com/', timeout=15)
            url = 'https://publicbidtracker.com/washington/open-bids/'
            resp = self.session.get(url, timeout=20, headers={
                'Referer': 'https://publicbidtracker.com/',
                'Accept-Encoding': 'gzip, deflate, br',
            })
            logger.info(f"  publicbidtracker: HTTP {resp.status_code} ({len(resp.content)} bytes)")
            if resp.status_code != 200 or not BeautifulSoup:
                return leads
            soup = BeautifulSoup(resp.content, 'html.parser')
            table = soup.find('table')
            if not table:
                logger.warning("  publicbidtracker: No table found")
                return leads
            rows = table.find_all('tr')
            logger.info(f"  publicbidtracker: Found {len(rows)} table rows")
            for row in rows:
                cells = row.find_all(['td', 'th'])
                if len(cells) < 3:
                    continue
                row_text = row.get_text(separator=' ', strip=True)
                if 'Bid #' in row_text or 'Organization' in row_text:
                    continue
                if not self._is_current_year(row_text):
                    continue
                desc = cells[2].get_text(strip=True) if len(cells) > 2 else row_text
                if not self._is_high_priority(row_text) and not self._is_high_priority(desc):
                    continue
                bid_num = cells[0].get_text(strip=True)
                link = row.find('a', href=True)
                link_url = link.get('href', '') if link else ''
                name = re.sub(r'\s+', ' ', desc).strip()[:120]
                if len(name) < 15:
                    name = f"WA Bid {bid_num}: {desc[:80]}"
                self._add_lead(leads, name, 'Washington State', 'Multi-County',
                               'WA WEBS (publicbidtracker.com)', link_url,
                               notes=f"Bid #{bid_num} - Review specs for scanning/drilling requirements",
                               text=row_text)
        except Exception as e:
            logger.warning(f"  publicbidtracker error: {e}")
        logger.info(f"  publicbidtracker: {len(leads)} leads found")
        return leads

    def scrape_seattle_buyline(self) -> List[Dict]:
        leads = []
        logger.info("Scraping thebuyline.seattle.gov (Seattle bids)...")
        try:
            url = 'https://thebuyline.seattle.gov/category/bids-and-proposals/'
            resp = self.session.get(url, timeout=20)
            logger.info(f"  Seattle BuyLine: HTTP {resp.status_code} ({len(resp.content)} bytes)")
            if resp.status_code != 200 or not BeautifulSoup:
                return leads
            soup = BeautifulSoup(resp.content, 'html.parser')
            for tag in soup.find_all(['nav', 'footer', 'header', 'script', 'style']):
                tag.decompose()
            # Grab post titles — h2 is the standard WordPress post title tag
            for post in soup.find_all(['h2', 'h3', 'article']):
                text = post.get_text(separator=' ', strip=True)
                if self._should_skip(text) or len(text) > 200:
                    continue
                if not self._is_current_year(text):
                    continue
                if not self._is_construction(text):
                    continue
                name = text.split('\n')[0].strip()[:120]
                link = post.find('a', href=True)
                link_url = link.get('href', '') if link else ''
                self._add_lead(leads, name, 'Seattle', 'King',
                               'Seattle Buy Line', link_url, text=text)
        except Exception as e:
            logger.warning(f"  Seattle BuyLine error: {e}")
        logger.info(f"  Seattle BuyLine: {len(leads)} leads found")
        return leads

    def scrape_seattle_consultants(self) -> List[Dict]:
        leads = []
        logger.info("Scraping consultants.seattle.gov (Seattle RFQs)...")
        try:
            url = 'https://consultants.seattle.gov/category/bids-proposals/'
            resp = self.session.get(url, timeout=20)
            logger.info(f"  Seattle Consultants: HTTP {resp.status_code} ({len(resp.content)} bytes)")
            if resp.status_code != 200 or not BeautifulSoup:
                return leads
            soup = BeautifulSoup(resp.content, 'html.parser')
            for tag in soup.find_all(['nav', 'footer', 'header', 'script', 'style']):
                tag.decompose()
            for post in soup.find_all(['h2', 'h3', 'article']):
                text = post.get_text(separator=' ', strip=True)
                if self._should_skip(text) or len(text) > 200:
                    continue
                if not self._is_current_year(text):
                    continue
                if not self._is_construction(text):
                    continue
                name = text.split('\n')[0].strip()[:120]
                link = post.find('a', href=True)
                link_url = link.get('href', '') if link else ''
                self._add_lead(leads, name, 'Seattle', 'King',
                               'Seattle Consultant Connection', link_url, text=text)
        except Exception as e:
            logger.warning(f"  Seattle Consultants error: {e}")
        logger.info(f"  Seattle Consultants: {len(leads)} leads found")
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

                if self._should_skip(text):
                    continue
                if len(text) > 250:
                    continue
                if not self._is_current_year(text):
                    continue
                if not self._is_construction(text):
                    continue

                href = ''
                if el.name == 'a':
                    href = el.get('href', '')
                else:
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

    def scrape_gc_projects(self) -> List[Dict]:
        leads = []
        logger.info("Scraping GC project pages...")
        gc_sources = [
            ('Sellen Construction', 'https://www.sellen.com/projects/', 'Seattle', 'King'),
            ('GLY Construction', 'https://www.gly.com/projects/', 'Bellevue', 'King'),
            ('Lease Crutcher Lewis', 'https://www.lewisbuilds.com/projects/', 'Seattle', 'King'),
            ('Howard S Wright', 'https://www.howardswright.com/projects/', 'Seattle', 'King'),
            ('BNBuilders', 'https://www.bnbuilders.com/projects/', 'Seattle', 'King'),
            ('Absher Construction', 'https://www.absherco.com/projects/', 'Tacoma', 'Pierce'),
            ('Exxel Pacific', 'https://www.exxelpacific.com/projects/', 'Bothell', 'King'),
            ('Venture General Contracting', 'https://www.venturegc.com/projects/', 'Tacoma', 'Pierce'),
            ('Parametrix', 'https://www.parametrix.com/projects/', 'Auburn', 'King'),
            ('Korsmo Construction', 'https://www.korsmoconstruction.com/projects/', 'Tacoma', 'Pierce'),
        ]
        for gc_name, gc_url, city, county in gc_sources:
            try:
                resp = self.session.get(gc_url, timeout=15)
                if resp.status_code != 200 or not BeautifulSoup:
                    continue
                soup = BeautifulSoup(resp.content, 'html.parser')
                for tag in soup.find_all(['nav', 'footer', 'header', 'script', 'style']):
                    tag.decompose()
                for el in soup.find_all(['h1', 'h2', 'h3', 'h4', 'a']):
                    text = el.get_text(strip=True)
                    if self._should_skip(text) or len(text) > 200:
                        continue
                    if not self._is_high_priority(text):
                        continue
                    link = el if el.name == 'a' else el.find('a', href=True)
                    link_url = link.get('href', '') if link else gc_url
                    if link_url and not link_url.startswith('http'):
                        link_url = urljoin(gc_url, link_url)
                    self._add_lead(leads, text, city, county,
                                   f'GC - {gc_name}', link_url, gc=gc_name, text=text)
            except Exception as e:
                logger.debug(f"  GC {gc_name} error: {e}")
        logger.info(f"  GC pages: {len(leads)} leads found")
        return leads

    def save_results(self, all_leads: List[Dict]):
        existing = []
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

        existing_names = {e.get('name', '') for e in existing}
        new_leads = [l for l in all_leads if l.get('name', '') not in existing_names]
        combined = new_leads + existing

        with open(FOUND_FILE, 'w') as f:
            json.dump(combined, f, indent=2)

        logger.info(f"Saved {len(new_leads)} new leads ({len(combined)} total in file)")
        return new_leads

    def run(self):
        logger.info("=" * 60)
        logger.info("Scan2Core Daily Lead Hunter v7.1 starting...")
        logger.info(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        logger.info("=" * 60)

        all_leads = []
        all_leads.extend(self.scrape_public_bid_tracker())
        all_leads.extend(self.scrape_seattle_buyline())
        all_leads.extend(self.scrape_seattle_consultants())
        all_leads.extend(self.scrape_all_city_pages())
        all_leads.extend(self.scrape_gc_projects())

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
