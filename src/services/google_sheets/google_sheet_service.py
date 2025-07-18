import os
import json
import google.auth
import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Google API Scopes
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.metadata.readonly"
]

# Path to token and credentials
TOKEN_FILE = "token.json"
CREDENTIALS_FILE = "credentials.json"


def authenticate():
    """Handles authentication, refreshes tokens if needed, and saves credentials."""
    creds = None

    # Load existing credentials
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # Request login if credentials are missing/expired
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(google.auth.transport.requests.Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES, redirect_uri="http://localhost")
            creds = flow.run_local_server(port=8080)

        # Save new token
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return creds


def list_sheets():
    """Fetch all Google Sheets available in Google Drive."""
    creds = authenticate()
    service = build("drive", "v3", credentials=creds)

    results = service.files().list(q="mimeType='application/vnd.google-apps.spreadsheet'",
                                   fields="files(id, name)").execute()
    return results.get("files", [])


def get_all_sheet_names(spreadsheet_id):
    """Retrieves all sheet names (tabs) from a given Google Sheet."""
    creds = authenticate()
    service = build("sheets", "v4", credentials=creds)

    try:
        sheet_metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheets = sheet_metadata.get("sheets", [])
        return [sheet["properties"]["title"] for sheet in sheets]
    except Exception as e:
        return {"error": str(e)}


def create_new_sheet(title, data):
    """Creates a new Google Sheet and inserts data."""
    creds = authenticate()
    service = build("sheets", "v4", credentials=creds)

    spreadsheet = {"properties": {"title": title}}
    sheet = service.spreadsheets().create(body=spreadsheet).execute()
    sheet_id = sheet["spreadsheetId"]

    update_sheet(sheet_id, "Sheet1", data, replace=True)
    return {"spreadsheet_id": sheet_id}


def update_sheet(spreadsheet_id, sheet_name, data, replace=False):
    """Updates an existing sheet. Supports append or replace."""
    creds = authenticate()
    service = build("sheets", "v4", credentials=creds)

    range_name = f"{sheet_name}!A1"
    body = {"values": data}

    if replace:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="RAW",
            body=body
        ).execute()
        return {"message": "Data replaced"}
    else:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body=body
        ).execute()
        return {"message": "Data appended"}
