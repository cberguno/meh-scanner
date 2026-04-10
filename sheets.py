import os
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import pandas as pd
from datetime import datetime

class GoogleSheets:
    def __init__(self):
        self.sheet_id = None
        self.service = None

    def setup(self):
        """Auto-creates sheet on first run or loads existing"""
        # TODO: implement service account + auto sheet creation
        print("✅ Google Sheets module loaded (setup coming next)")
        return True

    def append_deals(self, deals):
        """Append new deals to the sheet"""
        print(f"Would append {len(deals)} deals to Google Sheet")
        # TODO: real implementation in next phase
        for deal in deals:
            print(f" - {deal.get('site_name')} | {deal.get('rationale')[:80]}...")
