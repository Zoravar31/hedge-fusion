"""
Zerodha Kite Daily Login
========================
Run this every morning before trading to generate a fresh access token.

    python tools/kite_login.py

Access tokens expire at 6:30 PM IST each trading day.
"""

import os
import sys
import webbrowser
from pathlib import Path

from dotenv import load_dotenv, set_key

ENV_PATH = Path(__file__).parent.parent / ".env"
load_dotenv(ENV_PATH)


def main():
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        print("ERROR: Run: pip install kiteconnect")
        sys.exit(1)

    api_key    = os.getenv("KITE_API_KEY", "").strip()
    api_secret = os.getenv("KITE_API_SECRET", "").strip()

    if not api_key or not api_secret:
        print("ERROR: KITE_API_KEY or KITE_API_SECRET missing in .env")
        print("Get them at https://developers.kite.trade/")
        sys.exit(1)

    kite = KiteConnect(api_key=api_key)
    url  = kite.login_url()

    print("\n" + "="*60)
    print("  ZERODHA KITE LOGIN")
    print("="*60)
    print(f"\nOpening: {url}\n")
    webbrowser.open(url)
    print("After login, copy the 'request_token' from the redirect URL.")
    print("URL looks like: http://127.0.0.1:5000/?request_token=XXXX&status=success\n")

    token = input("Paste request_token here: ").strip()
    if not token:
        print("No token entered.")
        sys.exit(1)

    data = kite.generate_session(token, api_secret=api_secret)
    access_token = data["access_token"]
    set_key(str(ENV_PATH), "KITE_ACCESS_TOKEN", access_token)

    print(f"\n✅ Access token saved to {ENV_PATH}")
    print(f"   Token: {access_token[:12]}...")
    print("   Valid until 6:30 PM IST today.\n")


if __name__ == "__main__":
    main()
