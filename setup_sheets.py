#!/usr/bin/env python3
"""
Setup script to create Google Sheet and authenticate with Google Sheets API.
Run this once to initialize the spreadsheet for meh-scanner.
"""

import os
import json
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from dotenv import load_dotenv, set_key

# Load existing .env if it exists
load_dotenv()

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']

def get_google_service():
    """Authenticate and return Google Sheets service."""
    creds = None

    # Check for existing token
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    # If no valid credentials, run OAuth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # First time setup - need to create OAuth credentials
            print("🔐 No OAuth credentials found.")
            print("   You need to set up OAuth2 credentials for Google Sheets API.")
            print("   Follow these steps:")
            print("   1. Go to https://console.cloud.google.com/")
            print("   2. Create a new project or select existing one")
            print("   3. Enable Google Sheets API and Google Drive API")
            print("   4. Create OAuth 2.0 credentials (Desktop app)")
            print("   5. Download JSON and save as 'credentials.json' in this directory")
            print("   6. Run this script again\n")

            if not os.path.exists('credentials.json'):
                print("❌ credentials.json not found!")
                return None

            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)

        # Save credentials for next run
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    return build('sheets', 'v4', credentials=creds)

def create_spreadsheet(service):
    """Create a new spreadsheet for deal tracking."""
    spreadsheet = {
        'properties': {
            'title': 'Meh-Scanner Daily Deals',
            'locale': 'en_US'
        },
        'sheets': [
            {
                'properties': {
                    'sheetId': 0,
                    'title': 'Deals'
                }
            }
        ]
    }

    result = service.spreadsheets().create(body=spreadsheet).execute()
    sheet_id = result['spreadsheetId']

    # Add headers
    headers = [['Site Name', 'URL', 'Rationale', 'Added', 'Status']]
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range='Deals!A1:E1',
        valueInputOption='RAW',
        body={'values': headers}
    ).execute()

    return sheet_id

def main():
    print("📊 Meh-Scanner Google Sheets Setup")
    print("=" * 40)

    # Authenticate
    print("🔐 Authenticating with Google...")
    service = get_google_service()

    if not service:
        print("❌ Authentication failed. Please set up credentials.json and try again.")
        return

    print("✅ Authentication successful!")

    # Create spreadsheet
    print("📝 Creating spreadsheet...")
    sheet_id = create_spreadsheet(service)
    print(f"✅ Spreadsheet created: {sheet_id}")

    # Update .env
    env_file = '.env'
    if not os.path.exists(env_file):
        # Create from example
        if os.path.exists('.env.example'):
            with open('.env.example', 'r') as f:
                with open(env_file, 'w') as out:
                    out.write(f.read())

    set_key(env_file, 'GOOGLE_SHEET_ID', sheet_id)
    print(f"✅ Updated {env_file} with sheet ID")

    # Provide view link
    view_link = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
    print(f"\n🎉 Setup complete!")
    print(f"   View your sheet: {view_link}")
    print(f"   Run 'python main.py' to start scanning!")

if __name__ == '__main__':
    main()
