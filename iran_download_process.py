#!/usr/bin/env python3
"""Download and run RFI detection on S1 scenes over Iran."""
from pathlib import Path
from rfi_pipeline import run_pipeline

BASE_DIR = Path(__file__).parent
CATALOG = BASE_DIR / "output" / "iran_catalog.json"
DOWNLOAD_DIR = BASE_DIR / "output" / "iran_downloads"
OUTPUT_DIR = BASE_DIR / "output" / "iran_rfi"

if __name__ == "__main__":
    run_pipeline(CATALOG, DOWNLOAD_DIR, OUTPUT_DIR)
