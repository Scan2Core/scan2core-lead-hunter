#!/usr/bin/env python3
"""
Scan2Core Daily Lead Hunter
Finds WA construction projects requiring scanning AND core drilling
"""

import os
import json
import requests
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

WORKSPACE = os.getenv('GITHUB_WORKSPACE', '/tmp')
FOUND_FILE = os.path.join(WORKSPACE, 'found_projects.json')

class Scan2CoreBot:
    def __init__(self):
        self.found = self._load_found()
        self.new_leads = []
        
    def _load_found(self):
        if os.path.exists(FOUND_FILE):
            try:
                with open(FOUND_FILE, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def _save_found(self):
        try:
            with open(FOUND_FILE, 'w') as f:
                json.dump(self.found, f, indent=2, default=str)
            logger.info("✓ Saved found projects to found_projects.json")
        except Exception as e:
            logger.error(f"Could not save: {e}")
    
    def run(self):
        logger.info("=" * 60)
        logger.info("SCAN2CORE DAILY LEAD HUNT STARTING")
        logger.info("=" * 60)
        
        try:
            # Sample leads for now (will expand to real scraping)
            sample_leads = [
                {
                    'name': 'Bellevue Downtown Mixed-Use Development',
                    'location': '10200 NE 8th Street',
                    'city': 'Bellevue',
                    'county': 'King',
                    'gc': 'Turner Construction',
                    'owner': 'Bellevue Partners LLC',
                    'status': 'Active',
                    'type': 'Mixed-Use (Residential + Retail)',
                    'timeline': 'Jan 2024 - Dec 2026',
                    'priority': 9,
                    'evidence': 'Spec Section 03.3.1: "Contractor shall locate all post-tension strands via GPR prior to core drilling"',
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
                    'type': 'Hospital / Medical Facility',
                    'timeline': 'May 2024 - Sept 2027',
                    'priority': 10,
                    'evidence': 'Spec 05.5.2: "Prior to core drilling, GPR and utility locating services required"',
                    'source': 'Technical Specifications',
                    'found_date': datetime.now().isoformat()
                }
            ]
            
            # Check for new leads
            for lead in sample_leads:
                lead_id = f"{lead['name']}|{lead['gc']}"
                if lead_id not in self.found:
                    self.new_leads.append(lead)
                    self.found[lead_id] = {'name': lead['name'], 'found_date': datetime.now().isoformat()}
            
            logger.info(f"Found {len(self.new_leads)} new leads")
            self._save_found()
                
        except Exception as e:
            logger.error(f"Bot error: {e}", exc_info=True)
        
        logger.info("=" * 60)
        logger.info("DAILY HUNT COMPLETE")
        logger.info("=" * 60)

if __name__ == '__main__':
    bot = Scan2CoreBot()
    bot.run()
