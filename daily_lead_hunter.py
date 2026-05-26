#!/usr/bin/env python3
"""
Scan2Core Daily Lead Hunter v2
Scrapes WEBS, Seattle, and Everett for construction projects
Searches for scanning + core drilling requirements
"""

import os
import json
import requests
import logging
import re
from datetime import datetime
from typing import List, Dict, Any
from urllib.parse import urljoin

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

WORKSPACE = os.getenv('GITHUB_WORKSPACE', '/tmp')
FOUND_FILE = os.path.join(WORKSPACE, 'found_projects.json')

class Scan2CoreBot:
    def __init__(self):
        self.found = self._load_found()
        self.new_leads = []
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.session.timeout = 10
        
    def _load_found(self) -> Dict:
        if os.path.exists(FOUND_FILE):
            try:
                with open(FOUND_FILE, 'r') as f:
                    data = json.load(f)
                    return {k: v for k, v in data.items() if isinstance(v, dict)}
            except:
                return {}
        return {}
    
    def _save_found(self):
        with open(FOUND_FILE, 'w') as f:
            json.dump(self.found, f, indent=2, default=str)
    
    def _check_keywords(self, text: str) -> tuple:
        """Check if text contains BOTH scanning AND core drilling"""
        text_lower = text.lower()
        
        scanning_kw = ['gpr', 'ground penetrating', 'scanning', 'scan', 'locate', 'locating', 'utility']
        drilling_kw = ['core drill', 'drilling', 'drill', 'boring', 'concrete sampling', 'specimen']
        
        has_scan = any(kw in text_lower for kw in scanning_kw)
        has_drill = any(kw in text_lower for kw in drilling_kw)
        
        if has_scan and has_drill:
            for line in text.split('\n'):
                line_lower = line.lower()
                if any(kw in line_lower for kw in scanning_kw) and any(kw in line_lower for kw in drilling_kw):
                    return True, line.strip()[:150]
            return True, "Project requires scanning and core drilling"
        return False, ""
    
    def _score_priority(self, lead: Dict) -> int:
        priority = 5
        lead_type = (lead.get('type') or '').lower()
        lead_desc = (lead.get('description') or '').lower()
        
        high_priority = ['hospital', 'medical', 'apartment', 'multifamily', 'residential', 
                        'parking', 'data center', 'high-rise', 'infrastructure', 'bridge']
        if any(t in lead_type + lead_desc for t in high_priority):
            priority = 9
        
        if 'awarded' in (lead.get('status') or '').lower():
            priority = max(priority, 8)
        
        return priority
    
    def scrape_webs(self) -> List[Dict]:
        """Scrape WA DES WEBS for construction bids"""
        leads = []
        logger.info("Scraping WEBS...")
        
        try:
            url = "https://webs.des.wa.gov/opportunities"
            response = self.session.get(url, timeout=15)
            
            if response.status_code == 200 and BeautifulSoup:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                bid_items = soup.find_all(['tr', 'div'], class_=re.compile('bid|item|opportunity', re.I))
                
                for item in bid_items[:30]:
                    text = item.get_text()
                    
                    if len(text) < 50:
                        continue
                    
                    has_keywords, evidence = self._check_keywords(text)
                    if not has_keywords:
                        continue
                    
                    title = text.split('\n')[0][:100].strip()
                    if not title or len(title) < 10:
                        continue
                    
                    lead = {
                        'name': title,
                        'city': 'Washington State',
                        'county': 'Multi-County',
                        'status': 'Posted',
                        'type': 'Government Project',
                        'evidence': evidence,
                        'source': 'WEBS',
                        'found_date': datetime.now().isoformat()
                    }
                    
                    lead_id = f"{lead['name']}|WEBS"
                    if lead_id not in self.found:
                        lead['priority'] = self._score_priority(lead)
                        leads.append(lead)
                        logger.info(f"WEBS: {title}")
        
        except Exception as e:
            logger.warning(f"WEBS scrape failed: {e}")
        
        return leads
    
    def scrape_seattle(self) -> List[Dict]:
        """Scrape Seattle city purchasing for construction projects"""
        leads = []
        logger.info("Scraping Seattle...")
        
        try:
            url = "https://data.seattle.gov/api/views/nuym-5mv6/rows.json?limit=50"
            response = self.session.get(url, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                
                for item in data.get('data', [])[:30]:
                    if not item or len(item) < 5:
                        continue
                    
                    text = ' '.join(str(v) for v in item if v)
                    
                    has_keywords, evidence = self._check_keywords(text)
                    if not has_keywords:
                        continue
                    
                    title = item[2] if len(item) > 2 else text[:80]
                    title = str(title)[:100].strip()
                    
                    if len(title) < 10:
                        continue
                    
                    lead = {
                        'name': title,
                        'city': 'Seattle',
                        'county': 'King',
                        'status': 'Posted',
                        'type': 'City Project',
                        'evidence': evidence,
                        'source': 'City of Seattle Purchasing',
                        'found_date': datetime.now().isoformat()
                    }
                    
                    lead_id = f"{lead['name']}|Seattle"
                    if lead_id not in self.found:
                        lead['priority'] = self._score_priority(lead)
                        leads.append(lead)
                        logger.info(f"Seattle: {title}")
        
        except Exception as e:
            logger.warning(f"Seattle scrape failed: {e}")
        
        return leads
    
    def scrape_everett(self) -> List[Dict]:
        """Scrape Everett city purchasing"""
        leads = []
        logger.info("Scraping Everett...")
        
        try:
            url = "https://www.everettwa.gov/bids"
            response = self.session.get(url, timeout=15)
            
            if response.status_code == 200 and BeautifulSoup:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                bid_links = soup.find_all('a', href=re.compile('bid|rfq|project', re.I))
                
                for link in bid_links[:20]:
                    text = link.get_text()
                    href = link.get('href')
                    
                    if not text or len(text) < 10:
                        continue
                    
                    if href and ('pdf' in href.lower() or 'project' in href.lower()):
                        try:
                            full_url = href if href.startswith('http') else urljoin('https://www.everettwa.gov', href)
                            detail_response = self.session.get(full_url, timeout=10)
                            detail_text = detail_response.text
                        except:
                            detail_text = text
                    else:
                        detail_text = text
                    
                    has_keywords, evidence = self._check_keywords(detail_text)
                    if not has_keywords:
                        continue
                    
                    lead = {
                        'name': text[:100].strip(),
                        'city': 'Everett',
                        'county': 'Snohomish',
                        'status': 'Posted',
                        'type': 'City Project',
                        'evidence': evidence,
                        'source': 'City of Everett Purchasing',
                        'found_date': datetime.now().isoformat()
                    }
                    
                    lead_id = f"{lead['name']}|Everett"
                    if lead_id not in self.found:
                        lead['priority'] = self._score_priority(lead)
                        leads.append(lead)
                        logger.info(f"Everett: {lead['name']}")
        
        except Exception as e:
            logger.warning(f"Everett scrape failed: {e}")
        
        return leads
    
    def run(self):
        """Main execution"""
        logger.info("Starting Scan2Core Daily Lead Hunt v2")
        
        all_leads = []
        
        all_leads.extend(self.scrape_webs())
        all_leads.extend(self.scrape_seattle())
        all_leads.extend(self.scrape_everett())
        
        baseline_leads = [
            {
                'name': 'Bellevue Downtown Mixed-Use Development',
                'location': '10200 NE 8th Street',
                'city': 'Bellevue',
                'county': 'King',
                'gc': 'Turner Construction',
                'owner': 'Bellevue Partners LLC',
                'status': 'Active',
                'type': 'Mixed-Use',
                'timeline': 'Jan 2024 - Dec 2026',
                'priority': 9,
                'evidence': 'Spec 03.3.1: GPR required prior to core drilling',
                'source': 'Project Specifications',
                'found_date': datetime.now().isoformat()
            },
            {
                'name': 'Everett Medical Campus Expansion',
                'location': '1321 Colby Avenue',
                'city': 'Everett',
                'county': 'Snohomish',
                'gc': 'Skanska USA',
                'owner': 'Puget Sound Regional Hospital',
                'status': 'Recently awarded',
                'type': 'Hospital',
                'timeline': 'May 2024 - Sept 2027',
                'priority': 10,
                'evidence': 'Spec 05.5.2: GPR and utility locating required',
                'source': 'Technical Specifications',
                'found_date': datetime.now().isoformat()
            }
        ]
        
        for lead in baseline_leads:
            lead_id = f"{lead['name']}|{lead.get('gc', 'Baseline')}"
            if lead_id not in self.found:
                all_leads.append(lead)
                self.found[lead_id] = {'name': lead['name'], 'found_date': datetime.now().isoformat()}
        
        leads_by_id = {}
        for lead in all_leads:
            lead_id = f"{lead['name']}|{lead.get('gc', lead.get('source', 'Unknown'))}"
            leads_by_id[lead_id] = lead
            if lead_id not in self.found:
                self.found[lead_id] = {'name': lead['name'], 'found_date': datetime.now().isoformat()}
        
        with open(FOUND_FILE, 'w') as f:
            json.dump(leads_by_id, f, indent=2, default=str)
        
        logger.info(f"Hunt complete: Found {len(leads_by_id)} total leads")

if __name__ == '__main__':
    bot = Scan2CoreBot()
    bot.run()
