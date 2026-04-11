import os
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

class GoogleSheets:
    def __init__(self):
        self.sheet_id = os.getenv('GOOGLE_SHEET_ID')
        self.service = None

    def setup(self):
        """Load existing sheet credentials"""
        if not self.sheet_id:
            print("❌ GOOGLE_SHEET_ID not found in .env")
            print("   Run: python setup_sheets.py")
            return False

        try:
            if os.path.exists('token.json'):
                creds = Credentials.from_authorized_user_file('token.json')
                self.service = build('sheets', 'v4', credentials=creds)
                print(f"✅ Connected to sheet: {self.sheet_id}")
                return True
            else:
                print("❌ token.json not found. Run: python setup_sheets.py")
                return False
        except Exception as e:
            print(f"❌ Failed to setup Google Sheets: {str(e)}")
            return False

    def append_deals(self, deals):
        """Append new deals to the sheet"""
        if not self.sheet_id or not self.service:
            print("❌ Google Sheets not set up properly")
            return

        try:
            # Get existing data to avoid duplicates
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range='Deals!A:E'
            ).execute()
            existing_urls = set()
            if 'values' in result:
                for row in result['values'][1:]:  # Skip header
                    if len(row) >= 2:
                        existing_urls.add(row[1])  # URL column

            # Filter out duplicates
            new_deals = []
            for deal in deals:
                if deal.get('url') not in existing_urls:
                    new_deals.append(deal)

            if not new_deals:
                print("✅ No new deals to add")
                return

            # Prepare data for append
            values = []
            for deal in new_deals:
                values.append([
                    deal.get('site_name', ''),
                    deal.get('url', ''),
                    deal.get('rationale', ''),
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'New'
                ])

            # Append to sheet
            self.service.spreadsheets().values().append(
                spreadsheetId=self.sheet_id,
                range='Deals!A:E',
                valueInputOption='RAW',
                insertDataOption='INSERT_ROWS',
                body={'values': values}
            ).execute()

            print(f"✅ Added {len(new_deals)} new deals to Google Sheet")
            print(f"   View at: https://docs.google.com/spreadsheets/d/{self.sheet_id}")

        except Exception as e:
            print(f"❌ Failed to append deals: {str(e)}")
