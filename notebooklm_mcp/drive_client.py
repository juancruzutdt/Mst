"""Google Drive client for accessing NotebookLM sources."""

import io
import json
import os
import re
from pathlib import Path

CREDS_PATH = Path.home() / ".notebooklm_mcp" / "credentials.json"
TOKEN_PATH = Path.home() / ".notebooklm_mcp" / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
]


def _get_service():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDS_PATH.exists():
                raise FileNotFoundError(
                    f"Google credentials not found at {CREDS_PATH}. "
                    "Run setup_credentials.py first."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(creds.to_json())

    return build("drive", "v3", credentials=creds)


def is_authenticated() -> bool:
    if not CREDS_PATH.exists():
        return False
    try:
        _get_service()
        return True
    except Exception:
        return False


def _extract_file_id(file_id_or_url: str) -> str:
    """Extract file ID from a Drive URL or return as-is."""
    patterns = [
        r"/file/d/([a-zA-Z0-9_-]+)",
        r"/document/d/([a-zA-Z0-9_-]+)",
        r"/spreadsheets/d/([a-zA-Z0-9_-]+)",
        r"[?&]id=([a-zA-Z0-9_-]+)",
    ]
    for p in patterns:
        m = re.search(p, file_id_or_url)
        if m:
            return m.group(1)
    return file_id_or_url  # assume it's already a bare ID


def get_file_text(file_id_or_url: str) -> tuple[str, str]:
    """Download file content as text. Returns (name, text)."""
    from googleapiclient.http import MediaIoBaseDownload

    service = _get_service()
    file_id = _extract_file_id(file_id_or_url)

    meta = service.files().get(
        fileId=file_id,
        fields="name,mimeType",
    ).execute()
    name = meta["name"]
    mime = meta["mimeType"]

    # Google Workspace docs → export as plain text
    export_mimes = {
        "application/vnd.google-apps.document": "text/plain",
        "application/vnd.google-apps.spreadsheet": "text/csv",
        "application/vnd.google-apps.presentation": "text/plain",
    }
    if mime in export_mimes:
        data = service.files().export_media(
            fileId=file_id, mimeType=export_mimes[mime]
        ).execute()
        return name, data.decode("utf-8", errors="replace")

    # Binary files (PDF, txt, etc.)
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    raw = buf.getvalue()

    if mime == "application/pdf":
        return name, _extract_pdf_text(raw)

    # Assume UTF-8 text for other types
    return name, raw.decode("utf-8", errors="replace")


def _extract_pdf_text(data: bytes) -> str:
    """Best-effort PDF text extraction without heavy deps."""
    try:
        import pypdf  # optional
        reader = pypdf.PdfReader(io.BytesIO(data))
        return "\n\n".join(p.extract_text() or "" for p in reader.pages)
    except ImportError:
        pass
    # Fallback: regex extraction of raw PDF streams
    text = data.decode("latin-1", errors="replace")
    tokens = re.findall(r"\(((?:[^\\)]|\\.)*)\)", text)
    return " ".join(t.replace("\\n", "\n").replace("\\r", "") for t in tokens)


def list_drive_folder(folder_id: str) -> list[dict]:
    """List files inside a Drive folder."""
    service = _get_service()
    results = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id,name,mimeType,modifiedTime)",
        pageSize=100,
    ).execute()
    return results.get("files", [])
