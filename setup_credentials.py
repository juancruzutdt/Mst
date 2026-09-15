"""
Interactive setup: download Google OAuth credentials and authenticate.

Steps:
  1. Go to https://console.cloud.google.com/
  2. Create/select a project → APIs & Services → Enable "Google Drive API"
  3. Credentials → Create OAuth 2.0 Client ID (Desktop app)
  4. Download the JSON → place it at  ~/.notebooklm_mcp/credentials.json
     OR run this script and paste the path when prompted.
  5. Run this script: python setup_credentials.py
"""

import shutil
import sys
from pathlib import Path

CREDS_DIR = Path.home() / ".notebooklm_mcp"
CREDS_PATH = CREDS_DIR / "credentials.json"
TOKEN_PATH = CREDS_DIR / "token.json"


def main():
    print("=== NotebookLM MCP – Google Drive Setup ===\n")
    CREDS_DIR.mkdir(parents=True, exist_ok=True)

    if not CREDS_PATH.exists():
        src = input(
            "Paste the full path to your OAuth credentials JSON\n"
            "(downloaded from Google Cloud Console): "
        ).strip().strip('"')
        src_path = Path(src).expanduser().resolve()
        if not src_path.exists():
            print(f"File not found: {src_path}")
            sys.exit(1)
        shutil.copy(src_path, CREDS_PATH)
        print(f"Credentials copied to {CREDS_PATH}\n")
    else:
        print(f"Credentials already at {CREDS_PATH}\n")

    # Remove stale token so fresh auth happens
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()

    print("Opening browser for Google authentication…")
    sys.path.insert(0, str(Path(__file__).parent / "notebooklm_mcp"))
    import drive_client as dc
    try:
        svc = dc._get_service()
        about = svc.about().get(fields="user").execute()
        email = about.get("user", {}).get("emailAddress", "unknown")
        print(f"\nAuthenticated as: {email}")
        print("Setup complete. You can now use the MCP server.")
    except Exception as e:
        print(f"Authentication failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
