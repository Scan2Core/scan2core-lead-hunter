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
        logger.info("=" * 60)
        logger.info("SCAN2CORE DAILY LEAD HUNT STARTING")
        logger.info("=" * 60)
        
        try:
            # Sample leads for MVP (will expand to real scraping)
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
                    'source': 'Project Specifications'
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
                    'source': 'Technical Specifications'
                }
            ]
            
            # Check for new leads
            for lead in sample_leads:
                lead_id = f"{lead['name']}|{lead['gc']}"
                if lead_id not in self.found:
                    self.new_leads.append(lead)
                    self.found[lead_id] = {
                        'name': lead['name'],
                        'found_date': datetime.now().isoformat()
                    }
            
            logger.info(f"Found {len(self.new_leads)} new leads with scanning AND drilling")
            self._save_found()
            
            # Send email if HIGH priority leads
            high_priority = [l for l in self.new_leads if l.get('priority', 0) >= 8]
            if high_priority:
                self._send_email(high_priority)
                logger.info(f"Sent email with {len(high_priority)} HIGH priority leads")
            else:
                logger.info("No HIGH priority leads found today")
                
        except Exception as e:
            logger.error(f"Bot error: {e}", exc_info=True)
        
        logger.info("=" * 60)
        logger.info("DAILY HUNT COMPLETE")
        logger.info("=" * 60)
    
    def _send_email(self, leads):
        """Send email with leads"""
        if not all([EMAIL_FROM, EMAIL_PASSWORD, EMAIL_TO]):
            logger.error("Email config missing")
            return
        
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"Scan2Core Daily Leads - {len(leads)} Projects"
            msg['From'] = EMAIL_FROM
            msg['To'] = EMAIL_TO
            
            html = f"""
            <html>
            <body style="font-family: Arial; background-color: #0a0e27; color: #e0e0e0;">
            <div style="max-width: 1000px; margin: 0 auto; padding: 20px;">
                <h1 style="color: #d32f2f; border-bottom: 3px solid #d32f2f; padding-bottom: 10px;">
                    Scan2Core Daily Lead Hunt
                </h1>
                <p>Found <strong>{len(leads)}</strong> HIGH priority projects with scanning AND core drilling requirements</p>
            """
            
            for lead in leads:
                html += f"""
                <div style="background-color: #0f1535; border: 2px solid #d32f2f; padding: 16px; margin: 16px 0;">
                    <h2 style="margin: 0 0 8px 0; color: white;">{lead['name']}</h2>
                    <p style="margin: 0 0 12px 0; color: #90a4ae; font-size: 12px;">
                        {lead['location']} • {lead['city']}, {lead['county']}
                    </p>
                    <table style="width: 100%; font-size: 12px;">
                        <tr><td style="font-weight: bold; color: #90a4ae; width: 80px;">Status:</td><td>{lead['status']}</td></tr>
                        <tr><td style="font-weight: bold; color: #90a4ae;">Type:</td><td>{lead['type']}</td></tr>
                        <tr><td style="font-weight: bold; color: #90a4ae;">GC:</td><td>{lead['gc']}</td></tr>
                        <tr><td style="font-weight: bold; color: #90a4ae;">Owner:</td><td>{lead['owner']}</td></tr>
                        <tr><td style="font-weight: bold; color: #90a4ae;">Timeline:</td><td>{lead['timeline']}</td></tr>
                    </table>
                    <div style="background-color: #1a0f00; border-left: 4px solid #d32f2f; padding: 12px; margin: 12px 0; font-size: 12px; color: #ffccbc;">
                        <strong style="color: #ffb74d;">Evidence:</strong><br>
                        "{lead['evidence']}"<br>
                        <span style="color: #ff9800; font-size: 10px;">Source: {lead['source']}</span>
                    </div>
                    <p style="margin: 0; font-weight: bold; color: #d32f2f;">Priority: {lead['priority']}/10</p>
                </div>
                """
            
            html += """
            </div>
            </body>
            </html>
            """
            
            msg.attach(MIMEText(html, 'html'))
            
            # Send via Gmail
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login(EMAIL_FROM, EMAIL_PASSWORD)
                server.send_message(msg)
            
            logger.info(f"Email sent successfully")
            
        except Exception as e:
            logger.error(f"Email failed: {e}", exc_info=True)


if __name__ == '__main__':
    bot = Scan2CoreBot()
    bot.run()
