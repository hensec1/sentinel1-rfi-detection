#!/usr/bin/env python3
"""
Spatial RFI analysis for Norway Jammertest scenes.
Extracts RFI hotspot locations from S1 GRD data using annotation XML
geolocation grids, and maps them relative to the Bleik/Andøya jammer test site.
"""
import json
import gc
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np
from scipy.interpolate import RectBivariateSpline
import rasterio

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SUBSAMPLE = 4
BLEIK_LAT, BLEIK_LON = 69.27, 15.86

SCENES = [
    ("S1A_IW_GRDH_1SDV_20250910T161557_20250910T161622_060928_0796C3_E727.SAFE",
     "2025-09-10", "S1A", "DESC", "Pre-event baseline (18:15 local)"),
    ("S1C_IW_GRDH_1SDV_20250911T160700_20250911T160725_004079_0081B2_D4BC.SAFE",
     "2025-09-11", "S1C", "DESC", "VH=100 anomaly (18:07 local)"),
    ("S1A_IW_GRDH_1SDV_20250916T054528_20250916T054553_061009_0799FD_4FA1.SAFE",
     "2025-09-16", "S1A", "ASC", "Mid-event morning (07:45 local)"),
    ("S1C_IW_GRDH_1SDV_20250916T161506_20250916T161531_004152_0083F2_C908.SAFE",
     "2025-09-16", "S1C", "DESC", "Mid-event VH=100 (18:15 local)"),
    ("S1A_IW_GRDH_1SDV_20250918T052855_20250918T052920_061038_079B2F_ED03.SAFE",
     "2025-09-18", "S1A", "ASC", "Late event morning (07:28 local)"),
    ("S1A_IW_GRDH_1SDV_20250920T163219_20250920T163244_061074_079C9A_A1A1.SAFE",
     "2025-09-20", "S1A", "DESC", "Post-event baseline (18:32 local)"),
]

BASE_DIR = Path(__file__).parent
DOWNLOAD_DIR = BASE_DIR / "output" / "downloads"
OUTPUT_DIR = BASE_DIR / "output" / "jamertest"


def parse_geolocation_grid(annotation_xml):
    """Parse geolocation grid from S1 annotation XML, return interpolators for lat/lon."""
    tree = ET.parse(annotation_xml)
    root = tree.getroot()

    lines, pixels, lats, lons = [], [], [], []
    for gp in root.iter('geolocationGridPoint'):
        lines.append(int(gp.find('line').text))
        pixels.append(int(gp.find('pixel').text))
        lats.append(float(gp.find('latitude').text))
        lons.append(float(gp.find('longitude').text))

    lines = np.array(lines)
    pixels = np.array(pixels)
    lats = np.array(lats)
    lons = np.array(lons)

    unique_lines = np.unique(lines)
    unique_pixels = np.unique(pixels)

    lat_grid = np.zeros((len(unique_lines), len(unique_pixels)))
    lon_grid = np.zeros_like(lat_grid)

    line_idx = {v: i for i, v in enumerate(unique_lines)}
    pixel_idx = {v: i for i, v in enumerate(unique_pixels)}

    for l, p, la, lo in zip(lines, pixels, lats, lons):
        lat_grid[line_idx[l], pixel_idx[p]] = la
        lon_grid[line_idx[l], pixel_idx[p]] = lo

    lat_interp = RectBivariateSpline(unique_lines, unique_pixels, lat_grid, kx=1, ky=1)
    lon_interp = RectBivariateSpline(unique_lines, unique_pixels, lon_grid, kx=1, ky=1)

    return lat_interp, lon_interp


def find_annotation_xml(safe_dir, pol):
    """Find the annotation XML matching a polarization."""
    ann_dir = safe_dir / "annotation"
    pol_lower = pol.lower()
    for xml_path in ann_dir.glob("*.xml"):
        if pol_lower in xml_path.stem.lower():
            return xml_path
    return None


def intensity_to_db(data):
    with np.errstate(divide="ignore", invalid="ignore"):
        return 10.0 * np.log10(np.where(data > 0, data, np.nan))


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    a = (np.sin(dlat / 2) ** 2
         + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) * np.sin(dlon / 2) ** 2)
    return R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))


def analyze_scene(tif_path, lat_interp, lon_interp):
    """Load GRD, detect RFI bright pixels, convert to lat/lon using annotation grid."""
    with rasterio.open(tif_path) as src:
        h, w = src.height, src.width
        sub_h, sub_w = h // SUBSAMPLE, w // SUBSAMPLE
        data = src.read(
            1,
            out_shape=(sub_h, sub_w),
            resampling=rasterio.enums.Resampling.average,
        ).astype(np.float32)

    data_db = intensity_to_db(data)
    del data
    gc.collect()

    # Detect bright pixels
    valid = np.isfinite(data_db)
    vals = data_db[valid]
    if len(vals) == 0:
        return None

    med = np.median(vals)
    mad = np.median(np.abs(vals - med))
    std_est = mad * 1.4826
    threshold = med + 4.0 * std_est
    bright_mask = valid & (data_db > threshold)
    n_bright = int(np.sum(bright_mask))
    pct_bright = round(100.0 * n_bright / int(np.sum(valid)), 4)

    # Detect RFI azimuth lines
    row_means = np.nanmean(data_db, axis=1)
    valid_rows = np.isfinite(row_means)
    if np.any(valid_rows):
        row_med = np.nanmedian(row_means[valid_rows])
        row_mad = np.nanmedian(np.abs(row_means[valid_rows] - row_med))
        row_std = row_mad * 1.4826
        rfi_line_mask = valid_rows & (row_means > row_med + 3.0 * row_std)
        n_rfi_lines = int(np.sum(rfi_line_mask))
    else:
        rfi_line_mask = np.zeros(sub_h, dtype=bool)
        n_rfi_lines = 0

    del data_db
    gc.collect()

    # Convert bright pixel locations to original pixel coords then to lat/lon
    bright_rows, bright_cols = np.where(bright_mask)
    # Map subsampled coords back to original pixel space
    orig_rows = bright_rows * SUBSAMPLE + SUBSAMPLE // 2
    orig_cols = bright_cols * SUBSAMPLE + SUBSAMPLE // 2

    # Sample up to 5000 for efficiency
    if len(orig_rows) > 5000:
        idx = np.random.RandomState(42).choice(len(orig_rows), 5000, replace=False)
        orig_rows = orig_rows[idx]
        orig_cols = orig_cols[idx]

    # Interpolate lat/lon
    bp_lats = lat_interp.ev(orig_rows.astype(float), orig_cols.astype(float))
    bp_lons = lon_interp.ev(orig_rows.astype(float), orig_cols.astype(float))

    # RFI line center positions
    rfi_line_lats, rfi_line_lons = [], []
    if n_rfi_lines > 0:
        rfi_row_indices = np.where(rfi_line_mask)[0]
        center_col = np.full_like(rfi_row_indices, (sub_w // 2) * SUBSAMPLE + SUBSAMPLE // 2, dtype=float)
        orig_line_rows = (rfi_row_indices * SUBSAMPLE + SUBSAMPLE // 2).astype(float)
        rfi_line_lats = lat_interp.ev(orig_line_rows, center_col)
        rfi_line_lons = lon_interp.ev(orig_line_rows, center_col)

    return {
        "n_bright": n_bright,
        "pct_bright": pct_bright,
        "n_rfi_lines": n_rfi_lines,
        "bp_lats": bp_lats,
        "bp_lons": bp_lons,
        "rfi_line_lats": np.array(rfi_line_lats),
        "rfi_line_lons": np.array(rfi_line_lons),
    }


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_results = []

    for safe_name, date, sat, direction, note in SCENES:
        safe_dir = DOWNLOAD_DIR / safe_name
        if not safe_dir.exists():
            log.warning(f"Missing: {safe_dir}")
            continue

        meas_dir = safe_dir / "measurement"
        tifs = sorted(meas_dir.glob("*.tiff")) + sorted(meas_dir.glob("*.tif"))

        log.info(f"\n{'='*70}")
        log.info(f"Scene: {safe_name}")
        log.info(f"  Date: {date}  Sat: {sat}  Dir: {direction}  Note: {note}")

        for tif_path in tifs:
            fname = tif_path.stem.lower()
            pol = "VH" if "vh" in fname else "VV" if "vv" in fname else "?"

            ann_xml = find_annotation_xml(safe_dir, pol)
            if not ann_xml:
                log.warning(f"  No annotation XML for {pol}")
                continue

            log.info(f"\n  {pol}: parsing geolocation grid ...")
            lat_interp, lon_interp = parse_geolocation_grid(ann_xml)

            log.info(f"  {pol}: detecting RFI and mapping to lat/lon ...")
            result = analyze_scene(tif_path, lat_interp, lon_interp)
            if result is None:
                continue

            bp_lats = result["bp_lats"]
            bp_lons = result["bp_lons"]

            if len(bp_lats) > 0:
                distances = haversine_km(BLEIK_LAT, BLEIK_LON, bp_lats, bp_lons)
                mean_lat = float(np.mean(bp_lats))
                mean_lon = float(np.mean(bp_lons))
                mean_dist = float(np.mean(distances))
                median_dist = float(np.median(distances))
                min_dist = float(np.min(distances))
                max_dist = float(np.max(distances))
                pct_within_10km = float(100.0 * np.sum(distances < 10) / len(distances))
                pct_within_20km = float(100.0 * np.sum(distances < 20) / len(distances))
                pct_within_50km = float(100.0 * np.sum(distances < 50) / len(distances))
                lat_min, lat_max = float(np.min(bp_lats)), float(np.max(bp_lats))
                lon_min, lon_max = float(np.min(bp_lons)), float(np.max(bp_lons))
            else:
                mean_lat = mean_lon = mean_dist = median_dist = min_dist = max_dist = 0
                pct_within_10km = pct_within_20km = pct_within_50km = 0
                lat_min = lat_max = lon_min = lon_max = 0

            # RFI line distances
            if len(result["rfi_line_lats"]) > 0:
                line_dists = haversine_km(BLEIK_LAT, BLEIK_LON,
                                          result["rfi_line_lats"], result["rfi_line_lons"])
                line_mean_dist = float(np.mean(line_dists))
                line_min_dist = float(np.min(line_dists))
                line_pct_20km = float(100.0 * np.sum(line_dists < 20) / len(line_dists))
            else:
                line_mean_dist = line_min_dist = 0
                line_pct_20km = 0

            entry = {
                "product": safe_name.replace(".SAFE", ""),
                "date": date,
                "satellite": sat,
                "direction": direction,
                "polarization": pol,
                "note": note,
                "n_bright_pixels": result["n_bright"],
                "pct_bright": result["pct_bright"],
                "n_rfi_lines": result["n_rfi_lines"],
                "centroid_lat": round(mean_lat, 4),
                "centroid_lon": round(mean_lon, 4),
                "bbox": {
                    "lat_min": round(lat_min, 4), "lat_max": round(lat_max, 4),
                    "lon_min": round(lon_min, 4), "lon_max": round(lon_max, 4),
                },
                "dist_to_bleik_km": {
                    "mean": round(mean_dist, 1),
                    "median": round(median_dist, 1),
                    "min": round(min_dist, 1),
                    "max": round(max_dist, 1),
                },
                "pct_bright_within_10km": round(pct_within_10km, 1),
                "pct_bright_within_20km": round(pct_within_20km, 1),
                "pct_bright_within_50km": round(pct_within_50km, 1),
                "rfi_lines_mean_dist_km": round(line_mean_dist, 1),
                "rfi_lines_min_dist_km": round(line_min_dist, 1),
                "rfi_lines_pct_within_20km": round(line_pct_20km, 1),
            }
            all_results.append(entry)

            log.info(f"    Bright pixels: {result['n_bright']} ({result['pct_bright']:.3f}%)")
            log.info(f"    RFI lines: {result['n_rfi_lines']}")
            if len(bp_lats) > 0:
                log.info(f"    Centroid: {mean_lat:.3f}N, {mean_lon:.3f}E")
                log.info(f"    Bbox: {lat_min:.2f}-{lat_max:.2f}N, {lon_min:.2f}-{lon_max:.2f}E")
                log.info(f"    Dist to Bleik: mean={mean_dist:.1f}km  median={median_dist:.1f}km  min={min_dist:.1f}km")
                log.info(f"    Within 10km: {pct_within_10km:.1f}%  20km: {pct_within_20km:.1f}%  50km: {pct_within_50km:.1f}%")
            if len(result["rfi_line_lats"]) > 0:
                log.info(f"    RFI lines dist to Bleik: mean={line_mean_dist:.1f}km  min={line_min_dist:.1f}km  <20km: {line_pct_20km:.1f}%")

            gc.collect()

    # Save
    report_path = OUTPUT_DIR / "norway_rfi_spatial_report.json"
    with open(report_path, "w") as f:
        json.dump(all_results, f, indent=2)
    log.info(f"\nSaved to {report_path}")

    # Summary table
    print(f"\n{'='*130}")
    print(f"NORWAY JAMMERTEST - SPATIAL RFI vs BLEIK JAMMER SITE (69.27N, 15.86E)")
    print(f"{'='*130}")
    print(f"{'Date':<12} {'Sat':<5} {'Pol':<4} {'Bright':>7} {'%Brt':>6} {'Lines':>6} "
          f"{'Centroid':>16} {'MeanD':>7} {'MinD':>7} {'<10km':>6} {'<20km':>6} {'<50km':>6}  {'Note'}")
    print(f"{'-'*130}")
    for r in all_results:
        d = r["dist_to_bleik_km"]
        cen = f"{r['centroid_lat']:.2f}N,{r['centroid_lon']:.2f}E" if r["centroid_lat"] else "---"
        print(
            f"{r['date']:<12} {r['satellite']:<5} {r['polarization']:<4} "
            f"{r['n_bright_pixels']:>7} {r['pct_bright']:>5.3f}% {r['n_rfi_lines']:>6} "
            f"{cen:>16} {d['mean']:>6.1f}km {d['min']:>6.1f}km "
            f"{r['pct_bright_within_10km']:>5.1f}% {r['pct_bright_within_20km']:>5.1f}% "
            f"{r['pct_bright_within_50km']:>5.1f}%  {r['note']}"
        )
    print(f"{'='*130}")


if __name__ == "__main__":
    main()
