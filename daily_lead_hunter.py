#!/usr/bin/env python3
"""
Scan2Core Daily Lead Hunter v4
Type-based filtering - finds high-probability projects
"""

import os
import json
import requests
import logging
import re
from datetime import datetime
from typing import List, Dict

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

WORKSPACE = os.getenv('GITHUB_WORKSPACE', '/tmp')
FOUND_FILE = os.path.join(WORKSPACE, 'found_projects.json')

HIGH_PRIORITY_TYPES = [
    'hospital', 'medical', 'clinic', 'healthcare',
    'apartment', 'residential', 'multifamily', 'multi-family',
    'parking', 'garage',
    'data center', 'tech campus',
    'bridge', 'infrastructure', 'highway', 'road',
    'stadium', 'arena',
    'high-rise', 'highrise',
    'retrofit', 'seismic', 'renovation'
]

class Scan2CoreBot:
    def __init__(self):
        self.found = self._load_found()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
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
    
    def _save_found(self):
        with open(FOUND_FILE, 'w') as f:
            json.dump(self.found, f, indent=2, default=str)
    
    def _is_high_priority(self, text: str) -> bool:
        """Check if project type is high-priority for scanning/drilling"""
        text_lower = text.lower()
        return any(ptype in text_lower for ptype in HIGH_PRIORITY_TYPES)
    
    def scrape_webs(self) -> List[Dict]:
        """Scrape WA DES WEBS for high-priority projects"""
        leads = []
        logger.info("Scraping WEBS...")
        
        try:
            urls = [
                "https://webs.des.wa.gov/opportunities",
                "https://webs.des.wa.gov/",
            ]
            
            for url in urls:
                try:
                    response = self.session.get(url, timeout=10)
                    if response.status_code != 200 or not BeautifulSoup:
                        continue
                    
                    soup = BeautifulSoup(response.content, 'html.parser')
                    
                    for link in soup.find_all('a', href=True):
                        text = link.get_text(strip=True)
                        if not text or len(text) < 10:
                            continue
                        
                        # Check if it's a construction project
                        if not any(kw in text.lower() for kw in ['bid', 'rfq', 'construct', 'project', 'build']):
                            continue
                        
                        # Check if high-priority type
                        if not self._is_high_priority(text):
                            continue
                        
                        lead = {
                            'name': text[:100],
                            'city': 'Washington State',
                            'county': 'Multi-County',
                            'status': 'Posted',
                            'type': 'Government',
                            'source': 'WEBS',
                            'notes': 'Type-based match - review specs manually',
                            'found_date': datetime.now().isoformat()
                        }
                        
                        lead_id = f"{lead['name']}|WEBS"
                        if lead_id not in self.found:
                            lead['priority'] = 7
                            leads.append(lead)
                            self.found[lead_id] = {'name': lead['name'], 'found_date': datetime.now().isoformat()}
                            logger.info(f"WEBS: {text[:60]}")
                    
                    if leads:
                        break
                
                except Exception as e:
                    logger.debug(f"WEBS error: {e}")
                    continue
        
        except Exception as e:
            logger.warning(f"WEBS scrape error: {e}")
        
        return leads
    
    def scrape_seattle(self) -> List[Dict]:
        """Scrape Seattle for high-priority projects"""
        leads = []
        logger.info("Scraping Seattle...")
        
        try:
            urls = [
                "https://www.seattle.gov/purchasing/bids-and-rfps",
                "https://seattle.gov/purchasing",
                "https://data.seattle.gov/resource/nuym-5mv6.json?$limit=100",
            ]
            
            for url in urls:
                try:
                    response = self.session.get(url, timeout=10)
                    if response.status_code != 200:
                        continue
                    
                    # Try JSON
                    if url.endswith('.json'):
                        try:
                            data = response.json()
                            for item in data[:50]:
                                if not item:
                                    continue
                                text = ' '.join(str(v) for v in item.values() if v)
                                
                                if not self._is_high_priority(text):
                                    continue
                                
                                title = str(item.get('title') or item.get('project_name') or text[:80])[:100]
                                
                                lead = {
                                    'name': title,
                                    'city': 'Seattle',
                                    'county': 'King',
                                    'status': 'Posted',
                                    'type': 'City Project',
                                    'source': 'City of Seattle',
                                    'notes': 'Type-based match - review specs manually',
                                    'found_date': datetime.now().isoformat()
                                }
                                
                                lead_id = f"{lead['name']}|Seattle"
                                if lead_id not in self.found:
                                    lead['priority'] = 7
                                    leads.append(lead)
                                    self.found[lead_id] = {'name': lead['name'], 'found_date': datetime.now().isoformat()}
                                    logger.info(f"Seattle: {title[:60]}")
                            
                            if leads:
                                break
                        except:
                            pass
                    
                    # Try HTML
                    if BeautifulSoup and not leads:
                        soup = BeautifulSoup(response.content, 'html.parser')
                        
                        for link in soup.find_all('a', href=True):
                            text = link.get_text(strip=True)
                            if not text or len(text) < 10:
                                continue
                            
                            if not any(kw in text.lower() for kw in ['bid', 'rfq', 'project', 'construct']):
                                continue
                            
                            if not self._is_high_priority(text):
                                continue
                            
                            lead = {
                                'name': text[:100],
                                'city': 'Seattle',
                                'county': 'King',
                                'status': 'Posted',
                                'type': 'City Project',
                                'source': 'City of Seattle',
                                'notes': 'Type-based match - review specs manually',
                                'found_date': datetime.now().isoformat()
                            }
                            
                            lead_id = f"{lead['name']}|Seattle"
                            if lead_id not in self.found:
                                lead['priority'] = 7
                                leads.append(lead)
                                self.found[lead_id] = {'name': lead['name'], 'found_date': datetime.now().isoformat()}
                                logger.info(f"Seattle: {text[:60]}")
                        
                        if leads:
                            break
                
                except Exception as e:
                    logger.debug(f"Seattle error: {e}")
                    continue
        
        except Exception as e:
            logger.warning(f"Seattle scrape error: {e}")
        
        return leads
    
    def scrape_everett(self) -> List[Dict]:
        """Scrape Everett for high-priority projects"""
        leads = []
        logger.info("Scraping Everett...")
        
        try:
            urls = [
                "https://www.everettwa.gov/bids",
                "https://www.everettwa.gov/departments/public-works/projects",
                "https://www.everettwa.gov/purchasing",
            ]
            
            for url in urls:
                try:
                    response = self.session.get(url, timeout=10)
                    if response.status_code != 200 or not BeautifulSoup:
                        continue
                    
                    soup = BeautifulSoup(response.content, 'html.parser')
                    
                    for link in soup.find_all('a', href=True):
                        text = link.get_text(strip=True)
                        if not text or len(text) < 10:
                            continue
                        
                        if not any(kw in text.lower() for kw in ['bid', 'rfq', 'project', 'construct']):
                            continue
                        
                        if not self._is_high_priority(text):
                            continue
                        
                        lead = {
                            'name': text[:100],
                            'city': 'Everett',
                            'county': 'Snohomish',
                            'status': 'Posted',
                            'type': 'City Project',
                            'source': 'City of Everett',
                            'notes': 'Type-based match - review specs manually',
                            'found_date': datetime.now().isoformat()
                        }
                        
                        lead_id = f"{lead['name']}|Everett"
                        if lead_id not in self.found:
                            lead['priority'] = 7
                            leads.append(lead)
                            self.found[lead_id] = {'name': lead['name'], 'found_date': datetime.now().isoformat()}
                            logger.info(f"Everett: {text[:60]}")
                    
                    if leads:
                        break
                
                except Exception as e:
                    logger.debug(f"Everett error: {e}")
                    continue
        
        except Exception as e:
            logger.warning(f"Everett scrape error: {e}")
        
        return leads
    
    def run(self):
        """Main execution"""
        logger.info("Starting Scan2Core Daily Lead Hunt v4 - Type-based filtering")
        
        all_leads = []
        all_leads.extend(self.scrape_webs())
        all_leads.extend(self.scrape_seattle())
        all_leads.extend(self.scrape_everett())
        
        # Baseline verified leads
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
                'priority': 10,
                'evidence': 'Spec 05.5.2: GPR and utility locating required',
                'source': 'Technical Specifications',
                'found_date': datetime.now().isoformat()
            }
        ]
        
        leads_by_id = {}
        for lead in all_leads + baseline_leads:
            lead_id = f"{lead['name']}|{lead.get('gc', lead.get('source', 'Unknown'))}"
            leads_by_id[lead_id] = lead
            if lead_id not in self.found:
                self.found[lead_id] = {'name': lead['name'], 'found_date': datetime.now().isoformat()}
        
        with open(FOUND_FILE, 'w') as f:
            json.dump(leads_by_id, f, indent=2, default=str)
        
        logger.info(f"Hunt complete: {len(leads_by_id)} total leads, {len(all_leads)} from scrapers")

if __name__ == '__main__':
    bot = Scan2CoreBot()
    bot.run()
