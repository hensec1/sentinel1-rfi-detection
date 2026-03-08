#!/usr/bin/env python3
"""
Temporal z-score RFI detection for Norway Jammertest 2025 scenes.
Uses the same approach as temporal_rfi.py but adapted for 6 scenes
with mixed orbit geometries (ASC/DESC).

Only compares scenes within the same orbit direction, since ASC and DESC
have fundamentally different viewing geometries.
"""
import json, gc, logging
from pathlib import Path
from collections import defaultdict
import numpy as np
from rfi_pipeline import (
    parse_geolocation_grid, intensity_to_db, get_terrain_mask,
)
import rasterio

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SUBSAMPLE = 4       # Norway scenes use 4x (matches existing analysis)
GRID_RES = 0.005    # ~550m grid cells (finer than Iran/Gulf since area is smaller)
Z_THRESHOLD = 3.0
MIN_OBS = 2         # Lower threshold since we only have 3-4 scenes per orbit direction

BLEIK_LAT, BLEIK_LON = 69.27, 15.86

# Jammertest 2025 sites (from NPRA/jammertest-plan GitHub repo)
# Three transmitter sites within ~10km cluster on northern Andøya
JAMMER_SITES = {
    "Bleik": (69.2726, 15.9554),      # Village, meaconing/spoofing antenna site
    "Ramnan": (69.2480, 15.9200),      # ~3km S of Bleik, "Porcus Maior" 50W PRN jammer
    "Stave": (69.2630, 15.8100),       # ~5km W of Bleik, Site 3 meeting/test area
}

SCENES = [
    ("S1A_IW_GRDH_1SDV_20250910T161557_20250910T161622_060928_0796C3_E727.SAFE",
     "2025-09-10", "S1A", "DESC", "Pre-event baseline (18:15 local)"),
    ("S1C_IW_GRDH_1SDV_20250911T160700_20250911T160725_004079_0081B2_D4BC.SAFE",
     "2025-09-11", "S1C", "DESC", "Day 1 Jamertest, DURING jamming (18:07 local)"),
    ("S1A_IW_GRDH_1SDV_20250916T054528_20250916T054553_061009_0799FD_4FA1.SAFE",
     "2025-09-16", "S1A", "ASC", "Mid-event, OUTSIDE jamming hours (07:45 local)"),
    ("S1C_IW_GRDH_1SDV_20250916T161506_20250916T161531_004152_0083F2_C908.SAFE",
     "2025-09-16", "S1C", "DESC", "Mid-event, DURING jamming (18:15 local)"),
    ("S1A_IW_GRDH_1SDV_20250918T052855_20250918T052920_061038_079B2F_ED03.SAFE",
     "2025-09-18", "S1A", "ASC", "Late event, OUTSIDE jamming hours (07:28 local)"),
    ("S1A_IW_GRDH_1SDV_20250920T163219_20250920T163244_061074_079C9A_A1A1.SAFE",
     "2025-09-20", "S1A", "DESC", "Post-event baseline (18:32 local)"),
]

BASE_DIR = Path(__file__).parent
DOWNLOAD_DIR = BASE_DIR / "output" / "downloads"
OUTPUT_DIR = BASE_DIR / "output" / "jamertest"


def get_scene_grid_data(safe_dir):
    """Load a scene VH channel, return (relative_db, pixel_lats, pixel_lons, scene_median)."""
    meas_dir = safe_dir / "measurement"
    ann_dir = safe_dir / "annotation"

    vh_tif = vh_ann = None
    for t in sorted(meas_dir.glob("*.tiff")):
        if "vh" in t.stem.lower():
            vh_tif = t; break
    for a in ann_dir.glob("*.xml"):
        if "vh" in a.stem.lower():
            vh_ann = a; break
    if vh_tif is None or vh_ann is None:
        return None

    lat_interp, lon_interp = parse_geolocation_grid(vh_ann)

    with rasterio.open(vh_tif) as src:
        h, w = src.height, src.width
        sub_h, sub_w = h // SUBSAMPLE, w // SUBSAMPLE
        data = src.read(1, out_shape=(sub_h, sub_w),
                        resampling=rasterio.enums.Resampling.average).astype(np.float32)

    data_db = intensity_to_db(data)
    del data

    terrain_mask = get_terrain_mask(lat_interp, lon_interp, sub_h, sub_w)
    data_db[terrain_mask] = np.nan

    valid = np.isfinite(data_db)
    if np.sum(valid) < 1000:
        return None

    scene_median = float(np.median(data_db[valid]))
    relative_db = data_db - scene_median

    rows = (np.arange(sub_h) * SUBSAMPLE + SUBSAMPLE // 2).astype(float)
    cols = (np.arange(sub_w) * SUBSAMPLE + SUBSAMPLE // 2).astype(float)
    pixel_lats = lat_interp(rows, cols)
    pixel_lons = lon_interp(rows, cols)

    del terrain_mask
    gc.collect()
    return relative_db, pixel_lats, pixel_lons, scene_median


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    a = (np.sin(dlat / 2) ** 2
         + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) * np.sin(dlon / 2) ** 2)
    return R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Group scenes by orbit direction
    desc_scenes = [(s, d, sat, n) for s, d, sat, dir_, n in SCENES if dir_ == "DESC"]
    asc_scenes = [(s, d, sat, n) for s, d, sat, dir_, n in SCENES if dir_ == "ASC"]

    log.info(f"Descending scenes: {len(desc_scenes)}")
    log.info(f"Ascending scenes: {len(asc_scenes)}")

    # Compute grid bounds from all scenes
    grid_lat_min, grid_lat_max = 67.0, 72.0
    grid_lon_min, grid_lon_max = 10.0, 22.0
    n_rows = int((grid_lat_max - grid_lat_min) / GRID_RES)
    n_cols = int((grid_lon_max - grid_lon_min) / GRID_RES)
    log.info(f"Grid: {n_rows}x{n_cols} cells ({GRID_RES}° resolution)")

    all_results = []

    for direction, scene_group in [("DESC", desc_scenes), ("ASC", asc_scenes)]:
        log.info(f"\n{'='*70}")
        log.info(f"Processing {direction} orbit scenes ({len(scene_group)} scenes)")

        # ── Pass 1: Build baseline ──
        log.info("=== PASS 1: Building temporal baseline ===")
        cell_sum = np.zeros((n_rows, n_cols), dtype=np.float64)
        cell_sum_sq = np.zeros((n_rows, n_cols), dtype=np.float64)
        cell_count = np.zeros((n_rows, n_cols), dtype=np.int32)

        scene_data_cache = []  # cache for pass 2 (small enough for 3-4 scenes)

        for safe_name, date, sat, note in scene_group:
            safe_dir = DOWNLOAD_DIR / safe_name
            if not safe_dir.exists():
                log.warning(f"  Missing: {safe_dir}")
                continue
            log.info(f"  Pass1: {safe_name[:60]} ({date})")
            result = get_scene_grid_data(safe_dir)
            if result is None:
                log.warning(f"    Skipped")
                continue
            relative_db, pixel_lats, pixel_lons, scene_median = result

            grid_r = ((pixel_lats - grid_lat_min) / GRID_RES).astype(int)
            grid_c = ((pixel_lons - grid_lon_min) / GRID_RES).astype(int)
            valid = (np.isfinite(relative_db) &
                     (grid_r >= 0) & (grid_r < n_rows) &
                     (grid_c >= 0) & (grid_c < n_cols))
            vr = grid_r[valid]
            vc = grid_c[valid]
            vv = relative_db[valid].astype(np.float64)

            np.add.at(cell_sum, (vr, vc), vv)
            np.add.at(cell_sum_sq, (vr, vc), vv * vv)
            np.add.at(cell_count, (vr, vc), 1)

            scene_data_cache.append((safe_name, date, sat, note, relative_db, pixel_lats, pixel_lons))

        # Compute baseline
        cell_valid = cell_count >= MIN_OBS
        cell_mean = np.zeros((n_rows, n_cols), dtype=np.float64)
        cell_std = np.zeros((n_rows, n_cols), dtype=np.float64)

        cell_mean[cell_valid] = cell_sum[cell_valid] / cell_count[cell_valid]
        variance = cell_sum_sq[cell_valid] / cell_count[cell_valid] - cell_mean[cell_valid]**2
        variance = np.maximum(variance, 0)
        cell_std[cell_valid] = np.sqrt(variance)
        cell_std[cell_valid] = np.maximum(cell_std[cell_valid], 0.5)

        n_valid = int(np.sum(cell_valid))
        mean_obs = float(np.mean(cell_count[cell_valid])) if n_valid > 0 else 0
        log.info(f"Baseline: {n_valid:,} cells with {MIN_OBS}+ obs (avg {mean_obs:.1f} obs/cell)")

        # ── Pass 2: Score each scene ──
        log.info("=== PASS 2: Computing temporal z-scores ===")

        for safe_name, date, sat, note, relative_db, pixel_lats, pixel_lons in scene_data_cache:
            log.info(f"  Pass2: {safe_name[:60]} ({date})")

            grid_r = ((pixel_lats - grid_lat_min) / GRID_RES).astype(int)
            grid_c = ((pixel_lons - grid_lon_min) / GRID_RES).astype(int)

            valid = (np.isfinite(relative_db) &
                     (grid_r >= 0) & (grid_r < n_rows) &
                     (grid_c >= 0) & (grid_c < n_cols))

            zscores = np.full_like(relative_db, np.nan)
            vr = grid_r[valid]
            vc = grid_c[valid]

            baseline_ok = cell_valid[vr, vc]
            vr_ok = vr[baseline_ok]
            vc_ok = vc[baseline_ok]
            vals = relative_db[valid][baseline_ok]
            means = cell_mean[vr_ok, vc_ok]
            stds = cell_std[vr_ok, vc_ok]
            z = (vals - means) / stds

            valid_idx = np.where(valid)
            ok_rows = valid_idx[0][baseline_ok]
            ok_cols = valid_idx[1][baseline_ok]
            zscores[ok_rows, ok_cols] = z

            # RFI detections
            rfi_mask = np.isfinite(zscores) & (zscores > Z_THRESHOLD)
            n_rfi = int(np.sum(rfi_mask))

            # Extract RFI point coordinates
            points = []
            rfi_lats_arr = np.array([])
            rfi_lons_arr = np.array([])
            if n_rfi > 0:
                rfi_rows, rfi_cols = np.where(rfi_mask)
                rfi_z = zscores[rfi_rows, rfi_cols]

                ann_xml = None
                safe_dir = DOWNLOAD_DIR / safe_name
                for a in (safe_dir / "annotation").glob("*vh*.xml"):
                    ann_xml = a; break
                if ann_xml:
                    lat_interp, lon_interp = parse_geolocation_grid(ann_xml)
                    orig_r = (rfi_rows * SUBSAMPLE + SUBSAMPLE // 2).astype(float)
                    orig_c = (rfi_cols * SUBSAMPLE + SUBSAMPLE // 2).astype(float)

                    if len(orig_r) > 5000:
                        idx = np.random.RandomState(42).choice(len(orig_r), 5000, replace=False)
                        orig_r, orig_c, rfi_z = orig_r[idx], orig_c[idx], rfi_z[idx]

                    rfi_lats_arr = lat_interp.ev(orig_r, orig_c)
                    rfi_lons_arr = lon_interp.ev(orig_r, orig_c)
                    for la, lo in zip(rfi_lats_arr, rfi_lons_arr):
                        points.append([round(float(la), 5), round(float(lo), 5)])

            # Compute stats
            valid_z = zscores[np.isfinite(zscores)]
            mean_z = float(np.mean(valid_z)) if len(valid_z) > 0 else 0
            pct_rfi = round(100.0 * n_rfi / max(1, len(valid_z)), 3) if len(valid_z) > 0 else 0
            score = round(min(100.0, pct_rfi * 5.0 + mean_z * 2.0), 1)
            score = max(0, score)

            # Distance to jammer sites analysis
            bleik_stats = {}
            site_proximity = {}
            if len(rfi_lats_arr) > 0:
                # Compute distance to nearest jammer site for each point
                all_dists = []
                for sname, (slat, slon) in JAMMER_SITES.items():
                    d = haversine_km(slat, slon, rfi_lats_arr, rfi_lons_arr)
                    all_dists.append(d)
                    site_proximity[sname] = {
                        "mean_dist_km": round(float(np.mean(d)), 1),
                        "min_dist_km": round(float(np.min(d)), 1),
                        "pct_within_10km": round(float(100.0 * np.sum(d < 10) / len(d)), 1),
                        "pct_within_20km": round(float(100.0 * np.sum(d < 20) / len(d)), 1),
                    }
                # Min distance to ANY jammer site
                nearest = np.minimum.reduce(all_dists)
                bleik_stats = {
                    "mean_dist_km": round(float(np.mean(nearest)), 1),
                    "median_dist_km": round(float(np.median(nearest)), 1),
                    "min_dist_km": round(float(np.min(nearest)), 1),
                    "pct_within_10km": round(float(100.0 * np.sum(nearest < 10) / len(nearest)), 1),
                    "pct_within_20km": round(float(100.0 * np.sum(nearest < 20) / len(nearest)), 1),
                    "pct_within_50km": round(float(100.0 * np.sum(nearest < 50) / len(nearest)), 1),
                }

            # Mean z-score of pixels within 20km of Bleik
            near_bleik_z = []
            if n_valid > 0:
                # Check grid cells near Bleik
                bleik_r = int((BLEIK_LAT - grid_lat_min) / GRID_RES)
                bleik_c = int((BLEIK_LON - grid_lon_min) / GRID_RES)
                # 20km ≈ 0.18° lat, 0.55° lon at 69°N
                r_range = int(0.18 / GRID_RES)
                c_range = int(0.55 / GRID_RES)
                for dr in range(-r_range, r_range + 1):
                    for dc in range(-c_range, c_range + 1):
                        r, c = bleik_r + dr, bleik_c + dc
                        if 0 <= r < n_rows and 0 <= c < n_cols:
                            pass  # would need pixel-level access

            result_entry = {
                "product": safe_name.replace(".SAFE", ""),
                "date": date,
                "satellite": sat,
                "direction": direction,
                "note": note,
                "method": "temporal_zscore",
                "score": score,
                "n_rfi_pixels": n_rfi,
                "pct_rfi": pct_rfi,
                "mean_zscore": round(mean_z, 3),
                "n_bright": n_rfi,
                "points": points,
                "cluster_proximity": bleik_stats,
                "site_proximity": site_proximity,
                "meta": {"date": date, "time": safe_name.split("_")[4][9:15],
                         "product": safe_name.replace(".SAFE", "")[:40],
                         "satellite": sat},
            }
            all_results.append(result_entry)

            log.info(f"    Temporal z-score RFI: {n_rfi} pixels ({pct_rfi}%), score={score}")
            if bleik_stats:
                log.info(f"    Bleik proximity: mean={bleik_stats['mean_dist_km']}km  "
                         f"min={bleik_stats['min_dist_km']}km  "
                         f"<20km={bleik_stats['pct_within_20km']}%")

        del cell_sum, cell_sum_sq, cell_count, cell_mean, cell_std
        gc.collect()

    # Save temporal results (compatible with map generator format)
    temporal_path = OUTPUT_DIR / "rfi_temporal.json"
    with open(temporal_path, "w") as f:
        json.dump({
            "scenes": all_results,
            "method": "temporal_zscore",
            "grid_res": GRID_RES,
            "z_threshold": Z_THRESHOLD,
            "min_obs": MIN_OBS,
            "bleik_site": {"lat": BLEIK_LAT, "lon": BLEIK_LON},
        }, f, indent=2)

    total_pts = sum(len(s["points"]) for s in all_results)
    log.info(f"\nDone: {len(all_results)} scenes, {total_pts:,} temporal RFI points")
    log.info(f"Output: {temporal_path}")

    # Print comparison table
    print(f"\n{'='*120}")
    print(f"NORWAY JAMMERTEST 2025 — TEMPORAL Z-SCORE RFI ANALYSIS")
    print(f"Method: Per-location z-score against {direction}-orbit temporal baseline")
    print(f"Grid: {GRID_RES}° ({GRID_RES*111:.0f}km)  |  Z threshold: {Z_THRESHOLD}  |  Min obs: {MIN_OBS}")
    print(f"{'='*120}")
    print(f"{'Date':<12} {'Sat':<5} {'Dir':<5} {'Score':>6} {'RFI Px':>8} {'%RFI':>7} {'MeanZ':>7} "
          f"{'MnDist':>7} {'<20km':>6}  {'Context'}")
    print(f"{'-'*120}")
    for r in all_results:
        b = r.get("bleik_proximity", {})
        mn = f"{b.get('mean_dist_km', 0):.0f}km" if b else "---"
        p20 = f"{b.get('pct_within_20km', 0):.1f}%" if b else "---"
        print(f"{r['date']:<12} {r['satellite']:<5} {r['direction']:<5} "
              f"{r['score']:>5.1f}  {r['n_rfi_pixels']:>7} {r['pct_rfi']:>6.3f}% "
              f"{r['mean_zscore']:>6.3f} {mn:>7} {p20:>6}  {r['note']}")
    print(f"{'='*120}")


if __name__ == "__main__":
    main()
