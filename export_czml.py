#!/usr/bin/env python3
"""Export RFI temporal detections as CZML files (one per date) for Cesium.

Aggregates points into ~0.05° grid cells to keep file sizes manageable for git.
Each cell becomes a CZML rectangle entity with color intensity based on point density.
"""
import json, math
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).parent
INPUT = BASE_DIR / "output" / "iran_rfi" / "rfi_temporal.json"
OUTPUT_DIR = BASE_DIR / "output" / "czml"

CELL_SIZE = 0.05  # degrees (~5.5 km)


def density_to_rgba(count, max_count):
    """Map point density to color. Yellow -> Orange -> Red."""
    t = min(1.0, count / max(1, max_count * 0.5))
    if t < 0.33:
        r, g, b = 255, 220, 0
    elif t < 0.66:
        r, g, b = 255, int(220 - 120 * ((t - 0.33) / 0.33)), 0
    else:
        r, g, b = 255, int(100 - 100 * ((t - 0.66) / 0.34)), 0
    alpha = int(120 + 100 * t)
    return [r, g, b, alpha]


def make_czml_for_date(date_str, scenes):
    """Build a CZML document aggregating RFI points into grid cells."""
    doc = [{
        "id": "document",
        "name": f"Sentinel-1 RFI Detections — {date_str}",
        "version": "1.0",
        "description": (
            f"Radio frequency interference detections from Sentinel-1 SAR "
            f"over Iran on {date_str}. Temporal z-score method. "
            f"Aggregated into {CELL_SIZE}° grid cells."
        ),
    }]

    # Aggregate all points into grid cells
    cells = defaultdict(lambda: {"count": 0, "max_score": 0, "satellites": set()})
    for scene in scenes:
        meta = scene["meta"]
        score = scene["score"]
        sat = meta["satellite"]
        for lat, lon in scene["points"]:
            r = math.floor(lat / CELL_SIZE)
            c = math.floor(lon / CELL_SIZE)
            cell = cells[(r, c)]
            cell["count"] += 1
            cell["max_score"] = max(cell["max_score"], score)
            cell["satellites"].add(sat)

    if not cells:
        return doc

    max_count = max(c["count"] for c in cells.values())

    for i, ((r, c), cell) in enumerate(cells.items(), 1):
        lat_min = r * CELL_SIZE
        lon_min = c * CELL_SIZE
        lat_max = lat_min + CELL_SIZE
        lon_max = lon_min + CELL_SIZE
        rgba = density_to_rgba(cell["count"], max_count)

        doc.append({
            "id": f"cell_{i}",
            "name": f"RFI {date_str} [{lat_min:.2f},{lon_min:.2f}]",
            "description": (
                f"<b>RFI Grid Cell</b><br>"
                f"Date: {date_str}<br>"
                f"RFI points: {cell['count']}<br>"
                f"Max scene score: {cell['max_score']}<br>"
                f"Satellites: {', '.join(sorted(cell['satellites']))}<br>"
                f"Cell: {lat_min:.2f}–{lat_max:.2f}°N, {lon_min:.2f}–{lon_max:.2f}°E"
            ),
            "rectangle": {
                "coordinates": {
                    "wsenDegrees": [lon_min, lat_min, lon_max, lat_max]
                },
                "material": {
                    "solidColor": {
                        "color": {"rgba": rgba}
                    }
                },
                "outline": False,
                "height": 0,
            },
            "properties": {
                "date": date_str,
                "count": cell["count"],
                "max_score": cell["max_score"],
                "satellites": sorted(cell["satellites"]),
            },
        })

    return doc


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    data = json.load(open(INPUT))
    scenes = data["scenes"]
    total_pts = sum(len(s["points"]) for s in scenes)
    print(f"Loaded {len(scenes)} scenes, {total_pts:,} points")

    # Group by date
    by_date = defaultdict(list)
    for s in scenes:
        by_date[s["meta"]["date"]].append(s)

    for date_str in sorted(by_date):
        date_scenes = by_date[date_str]
        n_points = sum(len(s["points"]) for s in date_scenes)
        czml = make_czml_for_date(date_str, date_scenes)

        out_path = OUTPUT_DIR / f"iran_rfi_{date_str}.czml"
        with open(out_path, "w") as f:
            json.dump(czml, f)

        n_cells = len(czml) - 1  # minus document entity
        size_mb = out_path.stat().st_size / 1e6
        print(f"  {date_str}: {n_points:,} pts -> {n_cells:,} cells -> {out_path.name} ({size_mb:.1f} MB)")

    # Write manifest
    manifest = {
        "name": "Sentinel-1 RFI Detections — Iran",
        "method": data["method"],
        "z_threshold": data["z_threshold"],
        "grid_res_degrees": CELL_SIZE,
        "dates": {},
    }
    for date_str in sorted(by_date):
        date_scenes = by_date[date_str]
        manifest["dates"][date_str] = {
            "file": f"iran_rfi_{date_str}.czml",
            "scenes": len(date_scenes),
            "points": sum(len(s["points"]) for s in date_scenes),
        }

    manifest_path = OUTPUT_DIR / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nManifest: {manifest_path}")


if __name__ == "__main__":
    main()
