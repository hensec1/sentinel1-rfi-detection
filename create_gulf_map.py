#!/usr/bin/env python3
"""
Step 2: Generate an animated spy-movie-styled Leaflet map from RFI detection results.
Uses raw bright pixel coordinates and bins them client-side at zoom-dependent resolution.
"""
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output" / "gulf_rfi"

AOI_POLYGON = [
    [58.6970465541, 22.7140028154], [56.2888918439, 22.8837478126],
    [54.4520134168, 23.4105816041], [50.8566253139, 23.4441768189],
    [46.6869467453, 29.0498646354], [46.495368836, 29.7820088563],
    [47.1511308884, 30.8985754212], [48.6599066583, 31.6725442658],
    [49.9397818442, 31.2276210916], [51.0821571054, 30.3466276441],
    [52.3185925988, 29.2175923009], [53.4833969257, 28.3404240708],
    [54.989601775, 28.1234448784], [57.4530756038, 27.6101894193],
    [59.5268333339, 26.6529458329], [59.9479323942, 25.2433907358],
    [58.6970465541, 22.7140028154],
]
AOI_LEAFLET = [[lat, lon] for lon, lat in AOI_POLYGON]


def load_data():
    """Load scene data from points JSON or progress file."""
    points_path = OUTPUT_DIR / "gulf_rfi_points.json"
    progress_path = OUTPUT_DIR / "gulf_rfi_progress.json"

    # Prefer temporal z-score data if available
    temporal_path = OUTPUT_DIR / "rfi_temporal.json"
    if temporal_path.exists():
        data = json.load(open(temporal_path))
        scenes = data.get("scenes", [])
        if scenes:
            print(f"Using temporal z-score data ({data.get('method', '?')})")
            return scenes

    for path in [points_path, progress_path]:
        if path.exists():
            data = json.load(open(path))
            scenes = data.get("scenes", [])
            if scenes:
                return scenes
    return []


def generate_map(scenes):
    dates = sorted(set(s["meta"]["date"] for s in scenes if s.get("meta")))
    if not dates:
        print("No scene data to map.")
        return

    # Group scenes by date
    date_scenes = {d: [] for d in dates}
    for s in scenes:
        d = s["meta"]["date"]
        if d in date_scenes:
            date_scenes[d].append(s)

    # Per-date stats
    date_stats = {}
    for d in dates:
        ss = date_scenes[d]
        date_stats[d] = {
            "scenes": len(ss),
            "max_score": max((s["score"] for s in ss), default=0),
            "total_bright": sum(s.get("n_bright", 0) for s in ss),
            "total_points": sum(len(s.get("points", [])) for s in ss),
        }

    # Build per-date point arrays: {date: [[lat, lon], ...]}
    date_points = {}
    date_meta = {}
    for d in dates:
        all_pts = []
        meta_list = []
        for s in date_scenes[d]:
            for pt in s.get("points", []):
                all_pts.append([pt[0], pt[1]])
            meta_list.append(s.get("meta", {}))
        date_points[d] = all_pts
        date_meta[d] = {
            "score": max((s["score"] for s in date_scenes[d]), default=0),
            "n_scenes": len(date_scenes[d]),
            "satellites": list(set(m.get("satellite", "?") for m in meta_list)),
        }

    center_lat = sum(p[0] for p in AOI_LEAFLET) / len(AOI_LEAFLET)
    center_lon = sum(p[1] for p in AOI_LEAFLET) / len(AOI_LEAFLET)

    total_points = sum(len(v) for v in date_points.values())

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SIGINT // PERSIAN GULF RFI SURVEILLANCE</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
    @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ background: #000; overflow: hidden; }}
    #map {{ width: 100vw; height: 100vh; background: #0a0a0a; }}

    #scanlines {{
        position: fixed; top: 0; left: 0; width: 100%; height: 100%;
        background: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,255,136,0.015) 2px, rgba(0,255,136,0.015) 4px);
        pointer-events: none; z-index: 9999;
    }}

    #hud-header {{
        position: fixed; top: 0; right: 0; z-index: 1000;
        background: linear-gradient(180deg, rgba(0,0,0,0.9) 0%, rgba(0,0,0,0.6) 70%, transparent 100%);
        padding: 12px 20px 30px 20px;
        font-family: 'Share Tech Mono', 'Courier New', monospace;
        border-bottom: 1px solid rgba(0,255,136,0.2);
        text-align: right;
    }}
    #hud-header .title {{
        color: #00ff88; font-size: 14px; letter-spacing: 4px;
        text-shadow: 0 0 10px rgba(0,255,136,0.5);
    }}
    #hud-header .subtitle {{
        color: #667; font-size: 10px; letter-spacing: 2px; margin-top: 2px;
    }}

    #timeline {{
        position: fixed; bottom: 0; left: 0; right: 0; z-index: 1000;
        background: linear-gradient(0deg, rgba(0,0,0,0.95) 0%, rgba(0,0,0,0.7) 70%, transparent 100%);
        padding: 30px 20px 15px 20px;
        font-family: 'Share Tech Mono', 'Courier New', monospace;
    }}
    #timeline .date-display {{
        color: #00ff88; font-size: 20px; text-align: center;
        letter-spacing: 3px; margin-bottom: 8px;
        text-shadow: 0 0 15px rgba(0,255,136,0.6);
    }}
    #timeline .stats {{
        color: #556; font-size: 10px; text-align: center;
        letter-spacing: 2px; margin-bottom: 10px;
    }}
    #timeline input[type=range] {{
        width: 100%; -webkit-appearance: none; appearance: none;
        height: 3px; background: #1a1a1a; outline: none;
        border: 1px solid #333;
    }}
    #timeline input[type=range]::-webkit-slider-thumb {{
        -webkit-appearance: none; appearance: none;
        width: 16px; height: 16px; background: #00ff88;
        border-radius: 50%; cursor: pointer;
        box-shadow: 0 0 10px rgba(0,255,136,0.8), 0 0 20px rgba(0,255,136,0.4);
    }}
    #timeline .controls {{
        display: flex; justify-content: center; gap: 10px; margin-top: 8px;
    }}
    #timeline button {{
        background: transparent; border: 1px solid #00ff88; color: #00ff88;
        font-family: 'Share Tech Mono', monospace; font-size: 11px;
        padding: 4px 16px; cursor: pointer; letter-spacing: 2px;
        transition: all 0.2s;
    }}
    #timeline button:hover {{ background: rgba(0,255,136,0.15); }}
    #timeline button.active {{ background: rgba(0,255,136,0.25); }}

    .leaflet-control-layers {{
        background: rgba(10,10,10,0.95) !important;
        border: 1px solid rgba(0,255,136,0.3) !important;
        color: #00ff88 !important;
        font-family: 'Share Tech Mono', monospace !important;
        font-size: 11px !important;
        border-radius: 0 !important;
        box-shadow: 0 0 20px rgba(0,0,0,0.8) !important;
    }}
    .leaflet-control-layers-expanded {{ padding: 8px 12px !important; }}
    .leaflet-control-layers label {{ color: #aab !important; }}
    .leaflet-control-layers-separator {{ border-color: rgba(0,255,136,0.2) !important; }}

    #info-panel {{
        position: fixed; top: 65px; right: 10px; z-index: 1000;
        background: rgba(10,10,10,0.92); border: 1px solid rgba(0,255,136,0.25);
        padding: 10px 14px; font-family: 'Share Tech Mono', monospace;
        font-size: 10px; color: #667; max-width: 220px;
        letter-spacing: 1px; line-height: 1.6;
    }}
    #info-panel .label {{ color: #445; }}
    #info-panel .value {{ color: #00ff88; }}
    #info-panel .warn {{ color: #ff6600; }}
    #info-panel .crit {{ color: #ff0040; }}

    .leaflet-popup-content-wrapper {{
        background: transparent !important;
        border-radius: 0 !important;
        box-shadow: none !important;
    }}
    .leaflet-popup-content {{ margin: 0 !important; }}
    .leaflet-popup-tip {{ display: none; }}

    .corner {{ position: fixed; z-index: 999; pointer-events: none; }}
    .corner-tl {{ top: 55px; left: 10px; border-top: 2px solid rgba(0,255,136,0.3); border-left: 2px solid rgba(0,255,136,0.3); width: 30px; height: 30px; }}
    .corner-tr {{ top: 55px; right: 10px; border-top: 2px solid rgba(0,255,136,0.3); border-right: 2px solid rgba(0,255,136,0.3); width: 30px; height: 30px; }}
    .corner-bl {{ bottom: 90px; left: 10px; border-bottom: 2px solid rgba(0,255,136,0.3); border-left: 2px solid rgba(0,255,136,0.3); width: 30px; height: 30px; }}
    .corner-br {{ bottom: 90px; right: 10px; border-bottom: 2px solid rgba(0,255,136,0.3); border-right: 2px solid rgba(0,255,136,0.3); width: 30px; height: 30px; }}
</style>
</head>
<body>
<div id="scanlines"></div>
<div id="hud-header">
    <div class="title">SIGINT // SAR RFI SURVEILLANCE FEED</div>
    <div class="subtitle">SENTINEL-1 C-BAND INTERFERENCE DETECTION // PERSIAN GULF THEATER</div>
</div>
<div class="corner corner-tl"></div>
<div class="corner corner-tr"></div>
<div class="corner corner-bl"></div>
<div class="corner corner-br"></div>
<div id="info-panel">
    <div style="color:#00ff88;font-size:11px;margin-bottom:6px;border-bottom:1px solid #222;padding-bottom:4px;">// MISSION PARAMS</div>
    <span class="label">SENSOR:</span> <span class="value">SENTINEL-1A/C</span><br>
    <span class="label">BAND:</span> <span class="value">C-BAND 5.405 GHz</span><br>
    <span class="label">MODE:</span> <span class="value">IW GRDH</span><br>
    <span class="label">WINDOW:</span> <span class="value">{dates[0]} - {dates[-1]}</span><br>
    <span class="label">SCENES:</span> <span class="value">{len(scenes)}</span><br>
    <span class="label">RFI POINTS:</span> <span class="value">{total_points:,}</span><br>
    <div style="margin-top:6px;border-top:1px solid #222;padding-top:4px;">
    <span class="label">RFI DENSITY:</span><br>
    <span style="display:inline-block;width:10px;height:10px;background:#00ff88;margin:2px 4px 0 0;vertical-align:middle;"></span><span class="label">LOW</span><br>
    <span style="display:inline-block;width:10px;height:10px;background:#ff6600;margin:2px 4px 0 0;vertical-align:middle;"></span><span class="warn">MODERATE (top 15%)</span><br>
    <span style="display:inline-block;width:10px;height:10px;background:#ff0040;margin:2px 4px 0 0;vertical-align:middle;"></span><span class="crit">HIGH (top 5%)</span>
    </div>
</div>
<div id="map"></div>
<div id="timeline">
    <div class="date-display" id="current-date">{dates[0]}</div>
    <div class="stats" id="current-stats">LOADING...</div>
    <input type="range" id="date-slider" min="0" max="{len(dates)-1}" value="0" step="1">
    <div class="controls">
        <button id="btn-prev" onclick="stepDate(-1)">&lt; PREV</button>
        <button id="btn-play" onclick="togglePlay()">PLAY</button>
        <button id="btn-next" onclick="stepDate(1)">NEXT &gt;</button>
        <button id="btn-all" onclick="showAll()">ALL</button>
    </div>
</div>

<script>
    var dates = {json.dumps(dates)};
    var dateStats = {json.dumps(date_stats)};
    var dateMeta = {json.dumps(date_meta)};
    // Per-date raw points: [[lat, lon], ...]
    var datePoints = {json.dumps(date_points)};

    var currentIdx = 0;
    var playing = false;
    var playInterval = null;
    var showAllMode = false;

    var map = L.map('map', {{
        center: [{center_lat}, {center_lon}],
        zoom: 6,
        zoomControl: false,
        attributionControl: false
    }});

    // Basemaps
    var esriSat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
        maxZoom: 18, opacity: 0.7
    }});
    var cartoDark = L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_nolabels/{{z}}/{{x}}/{{y}}{{r}}.png', {{
        maxZoom: 19, opacity: 0.95
    }});
    var cartoLabels = L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_only_labels/{{z}}/{{x}}/{{y}}{{r}}.png', {{
        maxZoom: 19, opacity: 0.6
    }});

    esriSat.addTo(map);
    var darkOverlay = L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_nolabels/{{z}}/{{x}}/{{y}}{{r}}.png', {{
        maxZoom: 19, opacity: 0.45
    }}).addTo(map);
    cartoLabels.addTo(map);

    // AOI boundary
    var aoiPoly = L.polygon({json.dumps(AOI_LEAFLET)}, {{
        color: '#00ff88', weight: 1.5, fillOpacity: 0, dashArray: '8,4', opacity: 0.5
    }}).addTo(map);

    // Grid cell size by zoom level (degrees)
    function gridSize(zoom) {{
        if (zoom <= 5) return 0.5;
        if (zoom <= 6) return 0.25;
        if (zoom <= 7) return 0.1;
        if (zoom <= 8) return 0.05;
        if (zoom <= 9) return 0.025;
        if (zoom <= 10) return 0.01;
        if (zoom <= 11) return 0.005;
        if (zoom <= 12) return 0.002;
        return 0.001; // ~100m at equator
    }}

    // Bin points into grid cells, color by local density (points per cell)
    // Each point is [lat, lon]
    function binPoints(points, cellSize) {{
        var bins = {{}};
        for (var i = 0; i < points.length; i++) {{
            var lat = points[i][0];
            var lon = points[i][1];
            var r = Math.floor(lat / cellSize);
            var c = Math.floor(lon / cellSize);
            var key = r + ',' + c;
            if (!bins[key]) {{
                bins[key] = {{count: 0, latMin: r * cellSize, lonMin: c * cellSize}};
            }}
            bins[key].count++;
        }}

        // Compute percentiles for density-based coloring
        var counts = [];
        for (var k in bins) {{ counts.push(bins[k].count); }}
        counts.sort(function(a,b) {{ return a - b; }});
        var p50 = counts[Math.floor(counts.length * 0.5)] || 1;
        var p85 = counts[Math.floor(counts.length * 0.85)] || 1;
        var p95 = counts[Math.floor(counts.length * 0.95)] || 1;

        var features = [];
        for (var k in bins) {{
            var b = bins[k];
            var c = b.count;
            // Density-based threat: top 5% = HIGH, top 15% = MODERATE, rest = LOW
            var color, level;
            if (c >= p95) {{
                color = '#ff0040'; level = 'HIGH';
            }} else if (c >= p85) {{
                color = '#ff6600'; level = 'MODERATE';
            }} else {{
                color = '#00ff88'; level = 'LOW';
            }}
            var opacity = Math.max(0.15, Math.min(0.8, c / Math.max(1, p95) * 0.7));
            var maxCount = counts[counts.length - 1] || 1;
            var score = Math.round(Math.min(100, Math.log(1 + c) / Math.log(1 + maxCount) * 100));
            features.push({{
                type: 'Feature',
                geometry: {{
                    type: 'Polygon',
                    coordinates: [[
                        [b.lonMin, b.latMin],
                        [b.lonMin + cellSize, b.latMin],
                        [b.lonMin + cellSize, b.latMin + cellSize],
                        [b.lonMin, b.latMin + cellSize],
                        [b.lonMin, b.latMin]
                    ]]
                }},
                properties: {{
                    count: b.count,
                    opacity: opacity,
                    color: color,
                    level: level,
                    score: score
                }}
            }});
        }}
        return features;
    }}

    // Active layers
    var activeLayers = [];

    function clearActiveLayers() {{
        for (var i = 0; i < activeLayers.length; i++) {{
            map.removeLayer(activeLayers[i]);
        }}
        activeLayers = [];
    }}

    function renderDate(dateStr) {{
        var pts = datePoints[dateStr] || [];
        var zoom = map.getZoom();
        var cs = gridSize(zoom);
        var features = binPoints(pts, cs);

        var layer = L.geoJSON({{type: 'FeatureCollection', features: features}}, {{
            style: function(f) {{
                var p = f.properties;
                return {{
                    fillColor: p.color,
                    fillOpacity: p.opacity,
                    color: p.color,
                    weight: 0.5,
                    opacity: 0.6
                }};
            }},
            onEachFeature: function(f, layer) {{
                var p = f.properties;
                var scoreColor = p.score > 75 ? '#ff0040' : p.score > 40 ? '#ff6600' : '#00ff88';
                layer.bindPopup(
                    '<div style="font-family:Courier New,monospace;color:#00ff88;background:#0a0a0a;padding:8px;border:1px solid #00ff88;font-size:11px;">' +
                    '<div style="color:#ff0040;font-weight:bold;margin-bottom:4px;">// SIGNAL INTERCEPT</div>' +
                    '<b>DATE:</b> ' + dateStr + '<br>' +
                    '<b>RFI SCORE:</b> <span style="color:' + scoreColor + '">' + p.score + '/100</span><br>' +
                    '<b>DENSITY:</b> <span style="color:' + p.color + '">' + p.level + '</span><br>' +
                    '<b>RFI PIXELS:</b> ' + p.count + '<br>' +
                    '<b>GRID:</b> ' + cs.toFixed(4) + '&deg;' +
                    '</div>',
                    {{className: 'spy-popup'}}
                );
            }}
        }});
        layer.addTo(map);
        activeLayers.push(layer);
    }}

    function renderCurrent() {{
        clearActiveLayers();
        if (showAllMode) {{
            for (var i = 0; i < dates.length; i++) {{
                renderDate(dates[i]);
            }}
        }} else {{
            renderDate(dates[currentIdx]);
        }}
    }}

    // Re-render on zoom change for adaptive grid
    map.on('zoomend', function() {{
        renderCurrent();
    }});

    function updateDisplay(idx) {{
        currentIdx = idx;
        var d = dates[idx];
        document.getElementById('current-date').textContent = d;
        document.getElementById('date-slider').value = idx;
        var stats = dateStats[d] || {{scenes: 0, max_score: 0, total_bright: 0, total_points: 0}};
        var threatColor = stats.max_score > 60 ? '#ff0040' : stats.max_score > 30 ? '#ff6600' : '#00ff88';
        document.getElementById('current-stats').innerHTML =
            'PASSES: ' + stats.scenes + ' // PEAK RFI: <span style="color:' + threatColor + '">' +
            stats.max_score.toFixed(0) + '/100</span> // DETECTIONS: ' + stats.total_points;
        showAllMode = false;
        document.getElementById('btn-all').classList.remove('active');
        renderCurrent();
    }}

    function stepDate(delta) {{
        var newIdx = Math.max(0, Math.min(dates.length - 1, currentIdx + delta));
        updateDisplay(newIdx);
    }}

    function togglePlay() {{
        playing = !playing;
        var btn = document.getElementById('btn-play');
        if (playing) {{
            btn.textContent = 'PAUSE';
            btn.classList.add('active');
            playInterval = setInterval(function() {{
                var next = (currentIdx + 1) % dates.length;
                updateDisplay(next);
            }}, 2000);
        }} else {{
            btn.textContent = 'PLAY';
            btn.classList.remove('active');
            clearInterval(playInterval);
        }}
    }}

    function showAll() {{
        if (playing) togglePlay();
        showAllMode = !showAllMode;
        var btn = document.getElementById('btn-all');
        if (showAllMode) {{
            btn.classList.add('active');
            renderCurrent();
            document.getElementById('current-date').textContent = 'ALL DATES';
            var total = 0;
            for (var i = 0; i < dates.length; i++) total += (datePoints[dates[i]] || []).length;
            document.getElementById('current-stats').textContent = 'SHOWING ALL ' + total + ' DETECTIONS';
        }} else {{
            btn.classList.remove('active');
            updateDisplay(currentIdx);
        }}
    }}

    document.getElementById('date-slider').addEventListener('input', function(e) {{
        updateDisplay(parseInt(e.target.value));
    }});

    // Layer control
    var baseMaps = {{"SATELLITE": esriSat, "DARK": cartoDark}};
    var overlayMaps = {{"AOI BOUNDARY": aoiPoly, "LABELS": cartoLabels}};
    L.control.layers(baseMaps, overlayMaps, {{collapsed: false, position: 'topleft'}}).addTo(map);
    L.control.zoom({{position: 'bottomright'}}).addTo(map);

    // Initialize
    updateDisplay(0);
</script>
</body>
</html>"""

    map_path = OUTPUT_DIR / "gulf_rfi_map.html"
    with open(map_path, "w") as f:
        f.write(html)
    print(f"Map saved to {map_path}")
    print(f"  Dates: {dates}")
    print(f"  Scenes: {len(scenes)}")
    print(f"  Total points: {total_points:,}")


def main():
    scenes = load_data()
    if not scenes:
        print("No RFI data found yet. Run gulf_download_process.py first.")
        return
    generate_map(scenes)


if __name__ == "__main__":
    main()
