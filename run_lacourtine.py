#!/usr/bin/env python3
"""
Download and run RFI detection on Sentinel-1 scenes over La Courtine, France
during the May-Jun 2024 GNSS jamming tests.

La Courtine military camp: ~45.87N, 2.15E
Jamming test dates (UTC):
  May 27: 12:00-14:30
  May 28: 07:45-09:45, 13:00-14:30
  May 29: 07:45-09:45
  May 30: 13:00-14:30
  Jun 3:  07:45-09:45, 12:00-14:30
  Jun 4:  07:45-09:45, 12:00-14:30
  Jun 5:  12:00-14:30
  Jun 6:  07:45-09:45, 12:00-14:30
"""
import json
import gc
import os
import logging
import time
import zipfile
from pathlib import Path
import numpy as np
import rasterio
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SUBSAMPLE = 4

# Products to download and analyze
PRODUCTS = [
    {
        "name": "S1A_IW_GRDH_1SDV_20240525T174003_20240525T174028_054031_0691AB_51FE",
        "id": "35d010b1-954e-4875-b108-1313d9e6d741",
        "date": "2024-05-25",
        "time_utc": "17:40:03",
        "direction": "DESC",
        "note": "Pre-event baseline (2 days before test)",
    },
    {
        "name": "S1A_IW_GRDH_1SDV_20240529T060027_20240529T060052_054082_069370_B769",
        "id": "488fb43b-aa43-40b5-baf5-c4915311869c",
        "date": "2024-05-29",
        "time_utc": "06:00:27",
        "direction": "ASC",
        "note": "1h45m BEFORE morning jamming (07:45-09:45 UTC)",
    },
    {
        "name": "S1A_IW_GRDH_1SDV_20240530T174831_20240530T174856_054104_069435_6044",
        "id": "857b4b79-f522-469d-aab4-d447e2b3661d",
        "date": "2024-05-30",
        "time_utc": "17:48:31",
        "direction": "DESC",
        "note": "3h18m AFTER afternoon jamming (13:00-14:30 UTC)",
    },
    {
        "name": "S1A_IW_GRDH_1SDV_20240606T174002_20240606T174027_054206_0697B6_C704",
        "id": "9bb7220c-fed7-4e55-b36e-483359fd4c39",
        "date": "2024-06-06",
        "time_utc": "17:40:02",
        "direction": "DESC",
        "note": "3h10m AFTER last jamming session (12:00-14:30 UTC)",
    },
]

BASE_DIR = Path(__file__).parent
DOWNLOAD_DIR = BASE_DIR / "output" / "lacourtine_downloads"
OUTPUT_DIR = BASE_DIR / "output" / "lacourtine"


def load_credentials():
    env_path = BASE_DIR / ".env"
    creds = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                creds[k] = v
    return creds.get("CDSE_USER"), creds.get("CDSE_PASS")


def get_cdse_token(username, password):
    resp = requests.post(
        "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
        data={
            "client_id": "cdse-public",
            "username": username,
            "password": password,
            "grant_type": "password",
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def download_product(product_id, product_name, token):
    safe_dir = DOWNLOAD_DIR / f"{product_name}.SAFE"
    if safe_dir.exists():
        tifs = list((safe_dir / "measurement").glob("*.tiff")) + list(
            (safe_dir / "measurement").glob("*.tif")
        )
        if tifs:
            log.info(f"  Already downloaded: {product_name}")
            return safe_dir

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DOWNLOAD_DIR / f"{product_name}.zip"

    if not zip_path.exists():
        log.info(f"  Downloading {product_name} ...")
        url = f"https://zipper.dataspace.copernicus.eu/odata/v1/Products({product_id})/$value"
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(url, headers=headers, stream=True, timeout=300)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192 * 16):
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = 100 * downloaded / total
                    if downloaded % (50 * 1024 * 1024) < len(chunk):
                        log.info(f"    {downloaded / 1e6:.0f} / {total / 1e6:.0f} MB ({pct:.0f}%)")
        log.info(f"    Download complete: {zip_path.stat().st_size / 1e6:.0f} MB")

    log.info(f"  Extracting {zip_path.name} ...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(DOWNLOAD_DIR)

    zip_path.unlink()
    log.info(f"  Extracted to {safe_dir}")
    return safe_dir


def intensity_to_db(data):
    with np.errstate(divide="ignore", invalid="ignore"):
        return 10.0 * np.log10(np.where(data > 0, data, np.nan))


def detect_rfi_azimuth_lines(data_db):
    row_means = np.nanmean(data_db, axis=1)
    valid = np.isfinite(row_means)
    if not np.any(valid):
        return {"n_rfi_lines": 0, "pct_rfi_lines": 0.0, "total_lines": len(row_means)}
    med = np.nanmedian(row_means[valid])
    mad = np.nanmedian(np.abs(row_means[valid] - med))
    std_est = mad * 1.4826
    threshold = med + 3.0 * std_est
    rfi_mask = valid & (row_means > threshold)
    n_rfi = int(np.sum(rfi_mask))
    return {
        "n_rfi_lines": n_rfi,
        "pct_rfi_lines": round(100.0 * n_rfi / len(row_means), 2),
        "total_lines": len(row_means),
    }


def detect_rfi_bright_pixels(data_db):
    valid = np.isfinite(data_db)
    vals = data_db[valid]
    if len(vals) == 0:
        return {"n_bright_pixels": 0, "pct_bright": 0.0}
    med = np.median(vals)
    mad = np.median(np.abs(vals - med))
    std_est = mad * 1.4826
    threshold = med + 4.0 * std_est
    bright_mask = valid & (data_db > threshold)
    n_bright = int(np.sum(bright_mask))
    total = int(np.sum(valid))
    return {
        "n_bright_pixels": n_bright,
        "pct_bright": round(100.0 * n_bright / total, 4) if total > 0 else 0.0,
    }


def detect_rfi_spectral(data_db, n_sample_cols=64):
    nrows, ncols = data_db.shape
    if ncols < n_sample_cols:
        return {"peak_counts": 0}
    col_indices = np.linspace(0, ncols - 1, n_sample_cols, dtype=int)
    peak_count = 0
    for ci in col_indices:
        col = data_db[:, ci]
        valid = np.isfinite(col)
        if np.sum(valid) < 100:
            continue
        col_v = col[valid]
        med = np.median(col_v)
        mad = np.median(np.abs(col_v - med))
        std_est = mad * 1.4826
        threshold = med + 5.0 * std_est
        peak_count += int(np.sum(col_v > threshold))
    return {"peak_counts": peak_count}


def compute_score(azimuth, bright, spectral):
    score = min(100.0, (
        azimuth["pct_rfi_lines"] * 2.0
        + bright["pct_bright"] * 10.0
        + min(spectral["peak_counts"], 100) * 0.3
    ))
    if score > 60:
        severity = "HIGH"
    elif score > 30:
        severity = "MODERATE"
    elif score > 10:
        severity = "LOW"
    else:
        severity = "MINIMAL/NONE"
    return round(score, 1), severity


def find_tifs(safe_dir):
    meas_dir = safe_dir / "measurement"
    if not meas_dir.exists():
        return []
    return sorted(meas_dir.glob("*.tiff")) + sorted(meas_dir.glob("*.tif"))


def process_one_tif(tif_path):
    log.info(f"  Loading {tif_path.name} (subsampled {SUBSAMPLE}x) ...")
    with rasterio.open(tif_path) as src:
        h, w = src.height, src.width
        data = src.read(
            1,
            out_shape=(h // SUBSAMPLE, w // SUBSAMPLE),
            resampling=rasterio.enums.Resampling.average,
        ).astype(np.float32)
    log.info(f"    Shape: {data.shape}")

    data_db = intensity_to_db(data)
    del data
    gc.collect()

    azimuth = detect_rfi_azimuth_lines(data_db)
    bright = detect_rfi_bright_pixels(data_db)
    spectral = detect_rfi_spectral(data_db)
    del data_db
    gc.collect()

    score, severity = compute_score(azimuth, bright, spectral)
    return azimuth, bright, spectral, score, severity


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    username, password = load_credentials()
    if not username or not password:
        log.error("Missing CDSE_USER / CDSE_PASS in .env")
        return

    log.info("Authenticating with CDSE ...")
    token = get_cdse_token(username, password)
    log.info("Authenticated.")

    all_results = []

    for prod in PRODUCTS:
        product_name = prod["name"]
        log.info(f"\n{'='*70}")
        log.info(f"Product: {product_name}")
        log.info(f"  Date: {prod['date']}  Time: {prod['time_utc']} UTC  Dir: {prod['direction']}")
        log.info(f"  Context: {prod['note']}")

        safe_dir = download_product(prod["id"], product_name, token)
        tifs = find_tifs(safe_dir)
        log.info(f"  Found {len(tifs)} measurement TIFFs")

        for tif_path in tifs:
            fname = tif_path.stem.lower()
            pol = "VH" if "vh" in fname else "VV" if "vv" in fname else "?"

            log.info(f"\n  Processing {pol} channel ...")
            azimuth, bright, spectral, score, severity = process_one_tif(tif_path)

            log.info(f"    Azimuth lines: {azimuth['pct_rfi_lines']:.1f}% flagged")
            log.info(f"    Bright pixels: {bright['pct_bright']:.3f}%")
            log.info(f"    Spectral peaks: {spectral['peak_counts']}")
            log.info(f"    RFI Score: {score}/100 ({severity})")

            all_results.append({
                "product": product_name,
                "date": prod["date"],
                "time_utc": prod["time_utc"],
                "satellite": "S1A",
                "direction": prod["direction"],
                "polarization": pol,
                "note": prod["note"],
                "score": score,
                "severity": severity,
                "pct_rfi_lines": azimuth["pct_rfi_lines"],
                "pct_bright": bright["pct_bright"],
                "spectral_peaks": spectral["peak_counts"],
                "n_rfi_lines": azimuth["n_rfi_lines"],
            })
            gc.collect()

    # Save results
    report_path = OUTPUT_DIR / "lacourtine_rfi_report.json"
    with open(report_path, "w") as f:
        json.dump(all_results, f, indent=2)
    log.info(f"\nResults saved to {report_path}")

    # Print summary table
    print(f"\n{'='*100}")
    print("LA COURTINE GNSS JAMMING TEST - SENTINEL-1 RFI DETECTION RESULTS")
    print(f"{'='*100}")
    print(f"{'Date':<12} {'Time UTC':<10} {'Dir':<5} {'Pol':<4} {'Score':>6} {'Severity':<14} {'Context'}")
    print(f"{'-'*100}")
    for r in all_results:
        print(
            f"{r['date']:<12} {r['time_utc']:<10} {r['direction']:<5} {r['polarization']:<4} "
            f"{r['score']:>5.1f}  {r['severity']:<14} {r['note']}"
        )
    print(f"{'='*100}")


if __name__ == "__main__":
    main()
