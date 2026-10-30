"""Sends results to the Google Apps Script web app attached to the tracker Sheet."""
import json
import logging
import os

import requests

log = logging.getLogger(__name__)
# apps_script/Code.gs SCRIPT_VERSION this code expects (new postings for tracked jobs, Sources tab).
SCRIPT_VERSION = 2


def script_version():
    """Version of the Code.gs pasted into the Sheet; 0 if it predates versions or can't be reached."""
    url = os.environ.get("SHEET_WEBAPP_URL")
    if not url:
        return 0
    try:
        return int(requests.get(url, timeout=60).json().get("version", 0))
    except (requests.RequestException, ValueError, AttributeError):
        return 0


def send(payload):
    url = os.environ.get("SHEET_WEBAPP_URL")
    if not url:
        log.warning("SHEET_WEBAPP_URL not set; skipping upload")
        return False
    payload = {"secret": os.environ.get("SHARED_SECRET", ""), **payload}
    # Apps Script answers POSTs with a redirect to the result; requests follows it as GET.
    r = requests.post(url, data=json.dumps(payload), headers={"Content-Type": "application/json"}, timeout=300)
    r.raise_for_status()
    try:
        out = r.json()
    except ValueError:
        raise RuntimeError(f"Sheet web app returned non-JSON (is it deployed for 'Anyone'?): {r.text[:300]}")
    if not out.get("ok"):
        raise RuntimeError(f"Sheet web app error: {out}")
    log.info("sheet: %s", out)
    return True
