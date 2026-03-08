#!/usr/bin/env python3
"""
Step 1: Download and run RFI detection on S1 scenes over the Persian Gulf.
Produces a GeoJSON file with RFI polygons for each scene.
"""
import json, gc, os, logging, time, zipfile, sys
import xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np
from scipy.interpolate import RectBivariateSpline
import rasterio
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SUBSAMPLE = 8
SLOPE_THRESHOLD = 15.0  # degrees — mask terrain steeper than this
DEM_RES = 0.003  # degrees (~330m) for slope computation
DEM_TILE_PX = int(1.0 / DEM_RES)  # pixels per 1° tile at working res

BASE_DIR = Path(__file__).parent
DOWNLOAD_DIR = BASE_DIR / "output" / "gulf_downloads"
OUTPUT_DIR = BASE_DIR / "output" / "gulf_rfi"
DEM_CACHE = BASE_DIR / "output" / "dem_cache"
CATALOG = BASE_DIR / "output" / "gulf_catalog_simple.json"


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
        data={"client_id": "cdse-public", "username": username,
              "password": password, "grant_type": "password"},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def download_product(product_id, product_name, token):
    safe_dir = DOWNLOAD_DIR / f"{product_name}.SAFE"
    meas_dir = safe_dir / "measurement"
    if meas_dir.exists() and list(meas_dir.glob("*.tiff")):
        return safe_dir

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DOWNLOAD_DIR / f"{product_name}.zip"

    if not zip_path.exists():
        log.info(f"  Downloading {product_name[:50]}...")
        url = f"https://zipper.dataspace.copernicus.eu/odata/v1/Products({product_id})/$value"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = requests.get(url, headers=headers, stream=True, timeout=300)
            resp.raise_for_status()
            with open(zip_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=131072):
                    f.write(chunk)
        except Exception as e:
            log.error(f"  Download failed: {e}")
            if zip_path.exists():
                zip_path.unlink()
            return None
        if zip_path.exists():
            log.info(f"  Downloaded {zip_path.stat().st_size / 1e6:.0f} MB")
        else:
            log.error(f"  Download produced no file")
            return None

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(DOWNLOAD_DIR)
        zip_path.unlink()
    except Exception as e:
        log.error(f"  Extract failed: {e}")
        if zip_path.exists():
            zip_path.unlink()
        return None

    return safe_dir


def parse_geolocation_grid(annotation_xml):
    tree = ET.parse(annotation_xml)
    root = tree.getroot()
    lines, pixels, lats, lons = [], [], [], []
    for gp in root.iter('geolocationGridPoint'):
        lines.append(int(gp.find('line').text))
        pixels.append(int(gp.find('pixel').text))
        lats.append(float(gp.find('latitude').text))
        lons.append(float(gp.find('longitude').text))

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


def download_dem_tile(lat_floor, lon_floor):
    """Download a 1° Copernicus DEM 30m tile, return cached path or None."""
    DEM_CACHE.mkdir(parents=True, exist_ok=True)
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    name = f"Copernicus_DSM_COG_10_{ns}{abs(lat_floor):02d}_00_{ew}{abs(lon_floor):03d}_00_DEM"
    local = DEM_CACHE / f"{name}.tif"
    marker = DEM_CACHE / f"{name}.missing"
    if local.exists():
        return local
    if marker.exists():
        return None
    url = f"https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/{name}/{name}.tif"
    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code == 200:
            local.write_bytes(resp.content)
            log.info(f"  DEM tile: {ns}{abs(lat_floor):02d}_{ew}{abs(lon_floor):03d}")
            return local
    except Exception:
        pass
    marker.touch()
    return None


def load_dem_mosaic(lat_min, lat_max, lon_min, lon_max):
    """Load Copernicus DEM tiles covering extent, return (mosaic, lat_max, lon_min, res) or Nones."""
    m_lat_min = int(np.floor(lat_min))
    m_lat_max = int(np.ceil(lat_max))
    m_lon_min = int(np.floor(lon_min))
    m_lon_max = int(np.ceil(lon_max))
    n_lat = m_lat_max - m_lat_min
    n_lon = m_lon_max - m_lon_min
    tp = DEM_TILE_PX
    mosaic = np.full((n_lat * tp, n_lon * tp), np.nan, dtype=np.float32)
    got_any = False
    for lat_f in range(m_lat_min, m_lat_max):
        for lon_f in range(m_lon_min, m_lon_max):
            tile_path = download_dem_tile(lat_f, lon_f)
            if tile_path is None:
                continue
            try:
                with rasterio.open(tile_path) as src:
                    data = src.read(1, out_shape=(tp, tp),
                                    resampling=rasterio.enums.Resampling.average)
            except Exception:
                continue
            r_off = (m_lat_max - lat_f - 1) * tp
            c_off = (lon_f - m_lon_min) * tp
            mosaic[r_off:r_off + tp, c_off:c_off + tp] = data
            got_any = True
    if not got_any:
        return None, None, None, None
    return mosaic, float(m_lat_max), float(m_lon_min), DEM_RES


def compute_slope_deg(dem, res_deg, mean_lat):
    """Compute terrain slope in degrees from DEM array."""
    ns_m = res_deg * 111320.0
    ew_m = res_deg * 111320.0 * np.cos(np.radians(mean_lat))
    with np.errstate(invalid="ignore"):
        dy = np.gradient(dem, ns_m, axis=0)
        dx = np.gradient(dem, ew_m, axis=1)
        slope = np.degrees(np.arctan(np.sqrt(dx**2 + dy**2)))
    return np.where(np.isfinite(slope), slope, 0.0)


def get_terrain_mask(lat_interp, lon_interp, sub_h, sub_w):
    """Return boolean mask (True = steep terrain to exclude from RFI detection)."""
    rows = (np.arange(sub_h) * SUBSAMPLE + SUBSAMPLE // 2).astype(float)
    cols = (np.arange(sub_w) * SUBSAMPLE + SUBSAMPLE // 2).astype(float)
    pixel_lats = lat_interp(rows, cols)
    pixel_lons = lon_interp(rows, cols)

    lat_min, lat_max = float(pixel_lats.min()), float(pixel_lats.max())
    lon_min, lon_max = float(pixel_lons.min()), float(pixel_lons.max())
    mean_lat = (lat_min + lat_max) / 2.0

    mosaic, m_lat_max, m_lon_min, res = load_dem_mosaic(lat_min, lat_max, lon_min, lon_max)
    if mosaic is None:
        return np.zeros((sub_h, sub_w), dtype=bool)

    slope = compute_slope_deg(mosaic, res, mean_lat)

    # Sample slope at each radar pixel's geographic location
    dem_rows = ((m_lat_max - pixel_lats) / res).astype(int)
    dem_cols = ((pixel_lons - m_lon_min) / res).astype(int)
    dem_rows = np.clip(dem_rows, 0, slope.shape[0] - 1)
    dem_cols = np.clip(dem_cols, 0, slope.shape[1] - 1)
    sampled_slope = slope[dem_rows, dem_cols]

    n_masked = int(np.sum(sampled_slope > SLOPE_THRESHOLD))
    if n_masked > 0:
        pct = 100.0 * n_masked / sampled_slope.size
        log.info(f"  Terrain mask: {n_masked} pixels ({pct:.1f}%) slope >{SLOPE_THRESHOLD}°")
    return sampled_slope > SLOPE_THRESHOLD


def intensity_to_db(data):
    with np.errstate(divide="ignore", invalid="ignore"):
        return 10.0 * np.log10(np.where(data > 0, data, np.nan))


def process_scene(safe_dir, product_name, start_time, footprint):
    """Process VH channel, return GeoJSON features for RFI hotspots."""
    meas_dir = safe_dir / "measurement"
    ann_dir = safe_dir / "annotation"

    # Find VH tiff and annotation
    vh_tif = None
    vh_ann = None
    for t in sorted(meas_dir.glob("*.tiff")):
        if "vh" in t.stem.lower():
            vh_tif = t
            break
    if vh_tif is None:
        return None

    for a in ann_dir.glob("*.xml"):
        if "vh" in a.stem.lower():
            vh_ann = a
            break
    if vh_ann is None:
        return None

    log.info(f"  Processing VH: {vh_tif.name}")

    # Parse geolocation grid first (needed for terrain masking)
    lat_interp, lon_interp = parse_geolocation_grid(vh_ann)

    # Load and subsample
    with rasterio.open(vh_tif) as src:
        h, w = src.height, src.width
        sub_h, sub_w = h // SUBSAMPLE, w // SUBSAMPLE
        data = src.read(1, out_shape=(sub_h, sub_w),
                        resampling=rasterio.enums.Resampling.average).astype(np.float32)

    data_db = intensity_to_db(data)
    del data; gc.collect()

    # Get terrain mask to exclude steep slopes from RFI detection
    terrain_mask = get_terrain_mask(lat_interp, lon_interp, sub_h, sub_w)

    # Detect bright pixels (excluding steep terrain)
    valid = np.isfinite(data_db) & ~terrain_mask
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

    # Detect RFI lines (mask terrain before computing row means)
    masked_db = np.where(terrain_mask, np.nan, data_db)
    row_means = np.nanmean(masked_db, axis=1)
    valid_rows = np.isfinite(row_means)
    n_rfi_lines = 0
    if np.any(valid_rows):
        rm = np.nanmedian(row_means[valid_rows])
        rmad = np.nanmedian(np.abs(row_means[valid_rows] - rm))
        rs = rmad * 1.4826
        rfi_line_mask = valid_rows & (row_means > rm + 3.0 * rs)
        n_rfi_lines = int(np.sum(rfi_line_mask))

    # Compute score
    spectral_peaks = min(100, n_bright // 50)
    score = min(100.0, n_rfi_lines / max(1, sub_h) * 100 * 2.0 + pct_bright * 10.0 + spectral_peaks * 0.3)
    score = round(score, 1)

    del data_db; gc.collect()

    if n_bright < 10:
        return {"score": score, "n_bright": n_bright, "n_rfi_lines": n_rfi_lines,
                "pct_bright": pct_bright, "points": [],
                "meta": {"date": start_time[:10], "time": start_time[11:19],
                         "product": product_name[:40], "satellite": product_name[:3]}}

    # Get bright pixel coordinates (lat_interp already parsed above)
    bright_rows, bright_cols = np.where(bright_mask)
    orig_rows = (bright_rows * SUBSAMPLE + SUBSAMPLE // 2).astype(float)
    orig_cols = (bright_cols * SUBSAMPLE + SUBSAMPLE // 2).astype(float)

    # Sample for efficiency — keep up to 5000 points for fine-grained zoom
    if len(orig_rows) > 5000:
        idx = np.random.RandomState(42).choice(len(orig_rows), 5000, replace=False)
        orig_rows = orig_rows[idx]
        orig_cols = orig_cols[idx]

    bp_lats = lat_interp.ev(orig_rows, orig_cols)
    bp_lons = lon_interp.ev(orig_rows, orig_cols)

    # Store raw point coordinates (compact: rounded to ~10m precision)
    points = []
    for la, lo in zip(bp_lats, bp_lons):
        points.append([round(float(la), 5), round(float(lo), 5)])

    return {"score": score, "n_bright": n_bright, "n_rfi_lines": n_rfi_lines,
            "pct_bright": pct_bright, "points": points,
            "meta": {"date": start_time[:10], "time": start_time[11:19],
                     "product": product_name[:40], "satellite": product_name[:3]}}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    catalog = json.load(open(CATALOG))
    log.info(f"Catalog: {len(catalog)} products")

    username, password = load_credentials()
    if not username:
        log.error("Missing CDSE credentials in .env")
        return

    token = get_cdse_token(username, password)
    token_time = time.time()
    log.info("Authenticated with CDSE")

    all_scenes = []  # list of {meta, points, score, ...} per scene
    processed = 0

    # Check for existing progress
    progress_file = OUTPUT_DIR / "gulf_rfi_progress.json"
    done_products = set()
    if progress_file.exists():
        prev = json.load(open(progress_file))
        all_scenes = prev.get("scenes", [])
        done_products = {s["meta"]["product"] for s in all_scenes}
        log.info(f"Resuming: {len(done_products)} already processed")

    for i, prod in enumerate(catalog):
        name = prod["name"].replace(".SAFE", "")
        if name in done_products:
            continue

        # Refresh token every 8 minutes
        if time.time() - token_time > 480:
            token = get_cdse_token(username, password)
            token_time = time.time()
            log.info("Token refreshed")

        log.info(f"\n[{i+1}/{len(catalog)}] {name[:60]}")

        safe_dir = download_product(prod["id"], name, token)
        if safe_dir is None:
            continue

        result = process_scene(safe_dir, name, prod["start"], prod.get("footprint", {}))
        if result is None:
            continue

        all_scenes.append(result)
        processed += 1

        # Save progress every 5 scenes
        if processed % 5 == 0:
            with open(progress_file, "w") as f:
                json.dump({"scenes": all_scenes}, f)
            log.info(f"  Progress saved: {len(all_scenes)} scenes")

    # Save final output
    output_path = OUTPUT_DIR / "gulf_rfi_points.json"
    with open(output_path, "w") as f:
        json.dump({"scenes": all_scenes}, f)

    log.info(f"\nDone: {len(all_scenes)} scenes, {sum(len(s.get('points',[])) for s in all_scenes)} points")
    log.info(f"Output: {output_path}")


if __name__ == "__main__":
    main()
