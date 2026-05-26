#!/usr/bin/env python3
"""
Scan2Core Daily Lead Hunter v6
Real scrapers with verified URLs
Sources:
  - publicbidtracker.com (aggregates WEBS - 80+ WA agencies)
  - thebuyline.seattle.gov (Seattle construction bids blog)
  - consultants.seattle.gov (Seattle construction RFQs)
  - BXWA public city pages (Bellevue, Everett, Seattle, Kirkland, Redmond, Renton, Lynnwood etc.)
  - GC project pages (Sellen, GLY, Howard S Wright, BNBuilders, Lease Crutcher Lewis)
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
    'bid', 'rfq', 'rfp', 'construct', 'project', 'build', 'renovation',
    'contract', 'install', 'repair', 'upgrade', 'facility', 'phase',
    'structural', 'concrete', 'foundation', 'infrastructure', 'work',
    'demolish', 'improvement', 'modernization', 'replacement'
]

SKIP_EXACT = {
    'home', 'contact', 'about', 'login', 'search', 'menu',
    'next', 'previous', 'submit', 'more', 'back', 'top',
    'facebook', 'twitter', 'linkedin', 'instagram', 'youtube',
    'subscribe', 'newsletter', 'sitemap', 'privacy',
}


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
                    return {k: v for k, v in data.items() if isinstance(v, dict)}
            except:
                return {}
        return {}

    def _is_high_priority(self, text: str) -> bool:
        t = text.lower()
        return any(p in t for p in HIGH_PRIORITY_TYPES)

    def _is_construction(self, text: str) -> bool:
        t = text.lower()
        return any(k in t for k in CONSTRUCTION_KEYWORDS)

    def _should_skip(self, text: str) -> bool:
        t = text.lower().strip()
        if t in SKIP_EXACT:
            return True
        if len(t) < 15:
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
        self.found[lead_id] = {'name': name, 'found_date': datetime.now().isoformat()}
        logger.info(f"  FOUND [{source}]: {name[:70]}")

    def scrape_public_bid_tracker(self) -> List[Dict]:
        leads = []
        logger.info("Scraping publicbidtracker.com (WA state WEBS bids)...")
        try:
            url = 'https://publicbidtracker.com/washington/open-bids/'
            resp = self.session.get(url, timeout=20)
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
            for post in soup.find_all(['h1', 'h2', 'h3', 'h4', 'article']):
                text = post.get_text(separator=' ', strip=True)
                if not self._is_construction(text) or self._should_skip(text):
                    continue
                if not self._is_high_priority(text):
                    continue
                name = text.split('\n')[0].strip()[:120]
                link = post.find('a', href=True)
                link_url = link.get('href', '') if link else ''
                self._add_lead(leads, name, 'Seattle', 'King',
                               'Seattle Buy Line', link_url, text=text)
            for a in soup.find_all('a', href=True):
                text = a.get_text(strip=True)
                if len(text) < 20 or len(text) > 200:
                    continue
                if not self._is_construction(text) or not self._is_high_priority(text):
                    continue
                self._add_lead(leads, text, 'Seattle', 'King',
                               'Seattle Buy Line', a.get('href', ''), text=text)
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
            for post in soup.find_all(['h1', 'h2', 'h3', 'h4', 'article', 'li']):
                text = post.get_text(separator=' ', strip=True)
                if not self._is_construction(text) or self._should_skip(text):
                    continue
                if not self._is_high_priority(text):
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

    def scrape_bxwa_city(self, city_name: str, city_id: str, city: str, county: str) -> List[Dict]:
        leads = []
        try:
            session = requests.Session()
            session.headers.update(self.session.headers)
            city_url = f'http://www.bxwa.com/bxwa_toc/pub/{city_id}.html'
            resp = session.get(city_url, timeout=15)
            if resp.status_code != 200 or not BeautifulSoup:
                return leads
            soup = BeautifulSoup(resp.content, 'html.parser')
            toc_url = None
            for link in soup.find_all('a', href=True):
                href = link.get('href', '')
                if 'toc.html' in href or 'toc.php' in href:
                    match = re.search(r'd=([^&]+)', href)
                    if match:
                        toc_path = match.group(1)
                        toc_url = f'http://www.bxwa.com{toc_path}'
                    break
            if not toc_url:
                return leads
            terms_url = f'http://www.bxwa.com/bxwa_toc/terms/public_terms.php?d={toc_url.replace("http://www.bxwa.com", "")}&a={city_name}'
            session.get(terms_url, timeout=10)
            toc_resp = session.get(toc_url, timeout=15)
            if toc_resp.status_code != 200:
                return leads
            toc_soup = BeautifulSoup(toc_resp.content, 'html.parser')
            for tag in toc_soup.find_all(['nav', 'footer', 'header', 'script', 'style']):
                tag.decompose()
            for a in toc_soup.find_all('a', href=True):
                text = a.get_text(strip=True)
                href = a.get('href', '')
                if len(text) < 15 or len(text) > 300:
                    continue
                if self._should_skip(text) or not self._is_construction(text):
                    continue
                if not self._is_high_priority(text):
                    continue
                full_url = href if href.startswith('http') else urljoin(toc_url, href)
                self._add_lead(leads, text, city, county,
                               f'BXWA - {city_name}', full_url, text=text)
        except Exception as e:
            logger.debug(f"  BXWA {city_name} error: {e}")
        return leads

    def scrape_bxwa(self) -> List[Dict]:
        leads = []
        logger.info("Scraping BXWA (Builders Exchange WA)...")
        cities = [
            ('City of Seattle', '22', 'Seattle', 'King'),
            ('City of Bellevue', '281', 'Bellevue', 'King'),
            ('City of Everett', '20', 'Everett', 'Snohomish'),
            ('City of Kirkland', '312', 'Kirkland', 'King'),
            ('City of Redmond', '656', 'Redmond', 'King'),
            ('City of Renton', '438', 'Renton', 'King'),
            ('City of Lynnwood', '21', 'Lynnwood', 'Snohomish'),
            ('City of Bothell', '747', 'Bothell', 'King'),
            ('City of Auburn', '735', 'Auburn', 'King'),
            ('City of Kent', '547', 'Kent', 'King'),
            ('City of Federal Way', '622', 'Federal Way', 'King'),
            ('City of Tacoma', '27', 'Tacoma', 'Pierce'),
            ('City of Shoreline', '244', 'Shoreline', 'King'),
            ('King County', '282', 'Multi-City', 'King'),
            ('Snohomish County', '119', 'Multi-City', 'Snohomish'),
            ('Port of Seattle', '383', 'Seattle', 'King'),
            ('Sound Transit', '531', 'Multi-City', 'Multi-County'),
            ('Seattle Public Schools', '140', 'Seattle', 'King'),
            ('Bellevue School District', '233', 'Bellevue', 'King'),
        ]
        for city_name, city_id, city, county in cities:
            city_leads = self.scrape_bxwa_city(city_name, city_id, city, county)
            leads.extend(city_leads)
            if city_leads:
                logger.info(f"  BXWA {city_name}: {len(city_leads)} leads")
        logger.info(f"  BXWA total: {len(leads)} leads found")
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
            ('Venture General', 'ht
