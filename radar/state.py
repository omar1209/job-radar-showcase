"""Remembers which jobs were already processed (committed back to the repo).

Each entry: {"d": last updated, "s": status, ...}. Job entries may also hold "u" (ids of the
postings already examined), "l" (places the job is posted in) and, while the job is pending, "p"
(a LinkedIn posting to fetch again); "__name__" entries hold run-level notes such as when the job
boards were last read.
"""
import json
import os
from datetime import date, timedelta

PATH = os.path.join(os.path.dirname(__file__), "..", "data", "seen.json")
KEEP_DAYS = 60


class Seen:
    def __init__(self, path=PATH):
        self.path = path
        try:
            with open(path) as f:
                self.data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}

    def __contains__(self, key):
        return key in self.data

    def add(self, key, status, **extra):
        """Sets the status; other fields of the entry are kept unless given ("p" only while pending)."""
        entry = {k: v for k, v in self.data.get(key, {}).items() if k not in ("d", "s")}
        entry.update(extra, d=date.today().isoformat(), s=status)
        if not status.startswith("pending:"):
            entry.pop("p", None)
        self.data[key] = entry

    def meta(self, key):
        """The dict stored under a run-level key like "__health__" (created if missing)."""
        entry = self.data.setdefault(key, {"s": "meta"})
        entry["d"] = date.today().isoformat()
        return entry

    def save(self):
        cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
        self.data = {k: v for k, v in self.data.items() if v["d"] >= cutoff}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.data, f, separators=(",", ":"), sort_keys=True)
