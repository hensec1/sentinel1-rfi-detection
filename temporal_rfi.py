#!/usr/bin/env python3
"""
Temporal RFI detection using per-location z-scores across multi-pass SAR stack.
Terrain is stable across passes → temporal outliers = RFI.
Each scene is normalized by its own median to remove orbit-dependent incidence angle effects.
"""
import json, gc, logging, sys
from pathlib import Path
from collections import defaultdict
import numpy as np
from rfi_pipeline import (
    parse_geolocation_grid, intensity_to_db, get_terrain_mask,
    SUBSAMPLE
)
import rasterio

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

GRID_RES = 0.01       # degrees (~1.1 km grid cells)
Z_THRESHOLD = 3.0     # z-score threshold for RFI flagging
MIN_OBS = 3           # minimum observations per cell for valid statistics


def find_safe_dirs(download_dir):
    """Find all extracted .SAFE directories with measurement TIFFs."""
    dirs = []
    for d in sorted(download_dir.iterdir()):
        if d.is_dir() and d.name.endswith(".SAFE"):
            meas = d / "measurement"
            if meas.exists() and list(meas.glob("*.tiff")):
                dirs.append(d)
    return dirs


def get_scene_grid_data(safe_dir):
    """Load a scene, return (relative_db_2d, pixel_lats_2d, pixel_lons_2d, scene_median) or None."""
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


def accumulate_to_grid(relative_db, pixel_lats, pixel_lons,
                       grid_lat_min, grid_lon_min, n_rows, n_cols,
                       cell_sum, cell_sum_sq, cell_count):
    """Add one scene's data to the grid accumulators."""
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


def compute_scene_zscores(relative_db, pixel_lats, pixel_lons,
                          grid_lat_min, grid_lon_min, n_rows, n_cols,
                          cell_mean, cell_std, cell_valid):
    """Compute per-pixel z-scores for one scene against the temporal baseline."""
    grid_r = ((pixel_lats - grid_lat_min) / GRID_RES).astype(int)
    grid_c = ((pixel_lons - grid_lon_min) / GRID_RES).astype(int)

    valid = (np.isfinite(relative_db) &
             (grid_r >= 0) & (grid_r < n_rows) &
             (grid_c >= 0) & (grid_c < n_cols))

    zscores = np.full_like(relative_db, np.nan)
    vr = grid_r[valid]
    vc = grid_c[valid]

    # Only compute z-scores where baseline is valid
    baseline_ok = cell_valid[vr, vc]
    vr_ok = vr[baseline_ok]
    vc_ok = vc[baseline_ok]

    vals = relative_db[valid][baseline_ok]
    means = cell_mean[vr_ok, vc_ok]
    stds = cell_std[vr_ok, vc_ok]

    z = (vals - means) / stds

    # Write back to full array
    valid_idx = np.where(valid)
    ok_rows = valid_idx[0][baseline_ok]
    ok_cols = valid_idx[1][baseline_ok]
    zscores[ok_rows, ok_cols] = z

    return zscores


def run_temporal_analysis(download_dir, output_dir, catalog_path,
                          grid_lat_range, grid_lon_range):
    """
    Two-pass temporal RFI detection.
    Pass 1: Build per-cell baseline from all scenes.
    Pass 2: Score each scene against baseline, output high-z RFI detections.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    catalog = json.load(open(catalog_path))
    name_to_meta = {}
    for p in catalog:
        name = p["name"].replace(".SAFE", "")
        name_to_meta[name] = {"date": p["start"][:10], "time": p["start"][11:19],
                               "satellite": name[:3]}

    safe_dirs = find_safe_dirs(download_dir)
    log.info(f"Found {len(safe_dirs)} downloaded scenes")

    grid_lat_min, grid_lat_max = grid_lat_range
    grid_lon_min, grid_lon_max = grid_lon_range
    n_rows = int((grid_lat_max - grid_lat_min) / GRID_RES)
    n_cols = int((grid_lon_max - grid_lon_min) / GRID_RES)
    log.info(f"Grid: {n_rows}x{n_cols} cells ({GRID_RES}° resolution)")

    # ── Pass 1: Build baseline ──────────────────────────────────────────
    log.info("=== PASS 1: Building temporal baseline ===")
    cell_sum = np.zeros((n_rows, n_cols), dtype=np.float64)
    cell_sum_sq = np.zeros((n_rows, n_cols), dtype=np.float64)
    cell_count = np.zeros((n_rows, n_cols), dtype=np.int32)

    scene_list = []  # track which scenes we processed
    for i, safe_dir in enumerate(safe_dirs):
        log.info(f"  Pass1 [{i+1}/{len(safe_dirs)}] {safe_dir.name[:60]}")
        result = get_scene_grid_data(safe_dir)
        if result is None:
            log.warning(f"    Skipped (no valid data)")
            continue
        relative_db, pixel_lats, pixel_lons, scene_median = result
        accumulate_to_grid(relative_db, pixel_lats, pixel_lons,
                           grid_lat_min, grid_lon_min, n_rows, n_cols,
                           cell_sum, cell_sum_sq, cell_count)
        scene_list.append(safe_dir)
        del relative_db, pixel_lats, pixel_lons
        gc.collect()

    # Compute baseline statistics
    cell_valid = cell_count >= MIN_OBS
    cell_mean = np.zeros((n_rows, n_cols), dtype=np.float64)
    cell_std = np.zeros((n_rows, n_cols), dtype=np.float64)

    cell_mean[cell_valid] = cell_sum[cell_valid] / cell_count[cell_valid]
    variance = cell_sum_sq[cell_valid] / cell_count[cell_valid] - cell_mean[cell_valid]**2
    variance = np.maximum(variance, 0)  # numerical safety
    cell_std[cell_valid] = np.sqrt(variance)
    # Floor std to avoid division by zero / extreme z-scores from very stable areas
    cell_std[cell_valid] = np.maximum(cell_std[cell_valid], 0.5)

    n_valid_cells = int(np.sum(cell_valid))
    mean_obs = float(np.mean(cell_count[cell_valid])) if n_valid_cells > 0 else 0
    log.info(f"Baseline: {n_valid_cells:,} cells with {MIN_OBS}+ observations (avg {mean_obs:.1f} obs/cell)")

    del cell_sum, cell_sum_sq
    gc.collect()

    # ── Pass 2: Score anomalies ─────────────────────────────────────────
    log.info("=== PASS 2: Computing temporal z-scores ===")
    all_scenes = []
    max_zscore_grid = np.full((n_rows, n_cols), -np.inf, dtype=np.float32)

    for i, safe_dir in enumerate(scene_list):
        log.info(f"  Pass2 [{i+1}/{len(scene_list)}] {safe_dir.name[:60]}")
        result = get_scene_grid_data(safe_dir)
        if result is None:
            continue
        relative_db, pixel_lats, pixel_lons, scene_median = result

        zscores = compute_scene_zscores(
            relative_db, pixel_lats, pixel_lons,
            grid_lat_min, grid_lon_min, n_rows, n_cols,
            cell_mean, cell_std, cell_valid)

        # Find high-z pixels (RFI candidates)
        rfi_mask = np.isfinite(zscores) & (zscores > Z_THRESHOLD)
        n_rfi = int(np.sum(rfi_mask))

        # Update max z-score grid (project scene z-scores onto geographic grid)
        grid_r = ((pixel_lats - grid_lat_min) / GRID_RES).astype(int)
        grid_c = ((pixel_lons - grid_lon_min) / GRID_RES).astype(int)
        z_valid = (np.isfinite(zscores) &
                   (grid_r >= 0) & (grid_r < n_rows) &
                   (grid_c >= 0) & (grid_c < n_cols))
        vr = grid_r[z_valid].ravel()
        vc = grid_c[z_valid].ravel()
        vz = zscores[z_valid].ravel().astype(np.float32)
        # Use np.maximum.at for vectorized update
        np.maximum.at(max_zscore_grid, (vr, vc), vz)

        # Collect RFI point coordinates
        product_name = safe_dir.name.replace(".SAFE", "")
        meta = name_to_meta.get(product_name, {"date": "?", "time": "?", "satellite": "?"})

        points = []
        if n_rfi > 0:
            rfi_rows, rfi_cols = np.where(rfi_mask)
            rfi_z = zscores[rfi_rows, rfi_cols]

            # Map subsampled pixel coords to lat/lon via the annotation grid
            lat_interp, lon_interp = parse_geolocation_grid(
                next((safe_dir / "annotation").glob("*vh*.xml")))
            orig_r = (rfi_rows * SUBSAMPLE + SUBSAMPLE // 2).astype(float)
            orig_c = (rfi_cols * SUBSAMPLE + SUBSAMPLE // 2).astype(float)

            # Sample up to 5000 points
            if len(orig_r) > 5000:
                idx = np.random.RandomState(42).choice(len(orig_r), 5000, replace=False)
                orig_r, orig_c, rfi_z = orig_r[idx], orig_c[idx], rfi_z[idx]

            rfi_lats = lat_interp.ev(orig_r, orig_c)
            rfi_lons = lon_interp.ev(orig_r, orig_c)
            for la, lo in zip(rfi_lats, rfi_lons):
                points.append([round(float(la), 5), round(float(lo), 5)])

        # Compute scene-level temporal RFI score
        valid_z = zscores[np.isfinite(zscores)]
        mean_z = float(np.mean(valid_z)) if len(valid_z) > 0 else 0
        pct_rfi = round(100.0 * n_rfi / max(1, len(valid_z)), 3) if len(valid_z) > 0 else 0
        # Score: combination of % affected and mean z-score of affected area
        score = round(min(100.0, pct_rfi * 5.0 + mean_z * 2.0), 1)
        score = max(0, score)

        all_scenes.append({
            "score": score,
            "n_rfi_pixels": n_rfi,
            "pct_rfi": pct_rfi,
            "mean_zscore": round(mean_z, 2),
            "n_bright": n_rfi,
            "points": points,
            "meta": {"date": meta["date"], "time": meta["time"],
                     "product": product_name[:40], "satellite": meta["satellite"]},
        })
        log.info(f"    z-score RFI: {n_rfi} pixels ({pct_rfi}%), score={score}")

        del relative_db, pixel_lats, pixel_lons, zscores
        gc.collect()

    # Save results
    output_path = output_dir / "rfi_temporal.json"
    with open(output_path, "w") as f:
        json.dump({"scenes": all_scenes,
                    "method": "temporal_zscore",
                    "grid_res": GRID_RES,
                    "z_threshold": Z_THRESHOLD,
                    "min_obs": MIN_OBS,
                    "n_baseline_cells": n_valid_cells,
                    "mean_obs_per_cell": round(mean_obs, 1)}, f)

    # Save max z-score grid as a simple JSON (for map overlay)
    hotspot_cells = np.where((max_zscore_grid > -np.inf) & (max_zscore_grid > Z_THRESHOLD))
    hotspots = []
    for r, c in zip(hotspot_cells[0], hotspot_cells[1]):
        lat = grid_lat_min + (r + 0.5) * GRID_RES
        lon = grid_lon_min + (c + 0.5) * GRID_RES
        z = float(max_zscore_grid[r, c])
        hotspots.append([round(lat, 4), round(lon, 4), round(z, 1)])

    hotspot_path = output_dir / "rfi_temporal_hotspots.json"
    with open(hotspot_path, "w") as f:
        json.dump({"hotspots": hotspots, "grid_res": GRID_RES,
                    "z_threshold": Z_THRESHOLD}, f)

    total_pts = sum(len(s["points"]) for s in all_scenes)
    log.info(f"\nDone: {len(all_scenes)} scenes, {total_pts:,} temporal RFI points")
    log.info(f"Persistent hotspots: {len(hotspots):,} cells with max z > {Z_THRESHOLD}")
    log.info(f"Output: {output_path}")
    log.info(f"Hotspots: {hotspot_path}")


if __name__ == "__main__":
    BASE_DIR = Path(__file__).parent

    if len(sys.argv) > 1 and sys.argv[1] == "gulf":
        run_temporal_analysis(
            download_dir=BASE_DIR / "output" / "gulf_downloads",
            output_dir=BASE_DIR / "output" / "gulf_rfi",
            catalog_path=BASE_DIR / "output" / "gulf_catalog_simple.json",
            grid_lat_range=(22.0, 33.0),
            grid_lon_range=(46.0, 60.5),
        )
    else:
        # Default: Iran
        run_temporal_analysis(
            download_dir=BASE_DIR / "output" / "iran_downloads",
            output_dir=BASE_DIR / "output" / "iran_rfi",
            catalog_path=BASE_DIR / "output" / "iran_catalog.json",
            grid_lat_range=(25.0, 40.0),
            grid_lon_range=(44.0, 64.0),
        )
