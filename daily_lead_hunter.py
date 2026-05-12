#!/usr/bin/env python3
"""
Scan2Core Daily Lead Hunter - Simplified Version
Scrapes WA construction projects, finds scanning AND core drilling requirements
"""

import os
import json
import requests
import smtplib
import logging
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration from GitHub Secrets
EMAIL_FROM = os.getenv('SCAN2CORE_EMAIL')
EMAIL_PASSWORD = os.getenv('SCAN2CORE_EMAIL_PASSWORD')
EMAIL_TO = os.getenv('SCAN2CORE_EMAIL_TO')
WORKSPACE = os.getenv('GITHUB_WORKSPACE', '/tmp')
FOUND_FILE = os.path.join(WORKSPACE, 'found_projects.json')

class Scan2CoreBot:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.found = self._load_found()
        self.new_leads = []
        
    def _load_found(self):
        """Load previously found projects"""
        if os.path.exists(FOUND_FILE):
            try:
                with open(FOUND_FILE, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def _save_found(self):
        """Save found projects"""
        try:
            with open(FOUND_FILE, 'w') as f:
                json.dump(self.found, f, indent=2, default=str)
            logger.info("Saved found projects")
        except Exception as e:
            logger.error(f"Could not save: {e}")
    
    def run(self):
        """Main execution"""
        logger.info("=" * 60
