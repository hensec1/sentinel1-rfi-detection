# Sentinel-1 RFI Detection: Mapping GPS/GNSS Jamming from Space

Detect and map radio frequency interference (RFI) in Sentinel-1 SAR imagery using temporal baseline z-scores and DEM terrain masking. This pipeline can reveal GPS/GNSS jamming activity visible from orbit while filtering out false positives from mountainous terrain and orbit geometry effects.

## What's New: Temporal Z-Score Detection (v2)

The original pipeline used single-scene statistical thresholds that were sensitive to terrain, incidence angle, and orbit geometry — producing inflated scores, particularly in mountainous areas. The v2 pipeline addresses this with a two-pass temporal approach:

**Pass 1** builds a per-location baseline by accumulating radar backscatter across all scenes onto a geographic grid. Each scene is normalized by its own median dB to remove orbit-dependent incidence angle effects. Copernicus 30m DEM data masks steep terrain (slope >15°) to eliminate layover/foreshortening artifacts.

**Pass 2** scores each scene against the baseline. For every pixel, the z-score measures how many standard deviations the observation is from the temporal mean at that location. Only pixels with z > 3.0 are flagged as RFI candidates. Stable terrain, urban backscatter, and orbit geometry effects are all absorbed into the baseline.

## Results

### Iran & Persian Gulf (Feb–Mar 2026)

Temporal z-score analysis across 102 scenes (Iran) and 103 scenes (Persian Gulf), Feb 28 – Mar 7, 2026.

| Region | Scenes | Baseline Cells | Avg Obs/Cell | Temporal RFI Points | Persistent Hotspots (z>3) |
|--------|--------|---------------|-------------|--------------------|-----------------------|
| Iran | 102 | 1,686,368 | 335.5 | 482,688 | 501,581 |
| Persian Gulf | 103 | 1,555,217 | 372.8 | 449,435 | 379,690 |

The temporal model produces much lower per-scene scores (0–4/100 vs 30–97 in the original) by eliminating terrain-correlated false positives. The remaining detections represent genuine temporal anomalies — pixels that are anomalously bright relative to their own multi-pass baseline.

### Jammertest Norway (Sep 2025) — Updated Analysis

Norway's NPRA runs an annual controlled GNSS jamming exercise called [Jammertest](https://www.jamertest.no/) at three sites on northern Andøya: Bleik, Ramnan (50W "Porcus Maior" PRN jammer), and Stave. The 2025 test ran September 15–19 with full transmission schedules published on [GitHub](https://github.com/NPRA/jammertest-plan).

#### Original vs Temporal Z-Score Scores

| Date | Sat | Local Time | Old VH Score | New Score | Context |
|------|-----|-----------|-------------|-----------|---------|
| Sep 10 | S1A | 18:15 | 30 | 0.0 | Pre-event baseline |
| Sep 11 | S1C | 18:07 | 100 | 1.1 | 4 days before test week |
| Sep 16 | S1A | 07:45 | 78 | 0.6 | Test week, before daily sessions |
| Sep 16 | S1C | 18:15 | 100 | 0.6 | Test week, between sessions |
| Sep 18 | S1A | 07:29 | 36 | 0.0 | Test week, before daily sessions |
| Sep 20 | S1A | 18:32 | 38 | 0.0 | Post-event baseline |

The old VH=100 scores were terrain artifacts. With DEM masking and temporal normalization, those false positives disappear. However, the spatial analysis reveals something the original method couldn't isolate:

#### Spatial Concentration Near Jammer Sites

| Date | Context | % RFI <10km | % RFI <20km | Min Dist | Ramnan <10km |
|------|---------|------------|------------|----------|-------------|
| Sep 10 | Baseline | 0.1% | 0.2% | 3.3 km | 0.1% |
| Sep 16 DESC | Test week PM | **3.4%** | **7.6%** | **0.3 km** | **3.1%** |
| Sep 16 ASC | Test week AM | **3.8%** | **8.0%** | **0.4 km** | **3.4%** |
| Sep 20 | Baseline | 0.4% | 1.3% | 2.0 km | 0.4% |

Sep 16 shows **10–14x more RFI within 20km of the jammer cluster** than baselines, consistent across two independent orbits. The concentration is tightest around Ramnan, the 50W Porcus Maior site, with detections 300m from the transmitter location.

#### Timing Analysis

Cross-referencing the NPRA daily schedule against S1 overpass times:

- **Sep 16 evening (18:15 local):** The 50W Porcus Maior session was scheduled for 19:00 — only 45 minutes after the S1 overpass. At 18:15, only a 0W closedown procedure was active.
- **Sep 16 morning (07:45 local):** First scheduled transmission at 09:00 — 1h15m after the overpass.
- **No S1 overpass coincided with a scheduled transmission.**

The most parsimonious explanation for the spatial anomaly is equipment warm-up: the 50W amplifier chain at Ramnan being powered up before its scheduled test window, producing out-of-band spurious emissions. This is consistent with Sep 18 (same time window, only milliwatt equipment scheduled) showing no spatial anomaly.

See [docs/jammertest_analysis.md](docs/jammertest_analysis.md) for the full analysis.

## Detection Pipeline

### Temporal Z-Score Method (Recommended)

```
temporal_rfi.py — Two-pass temporal baseline RFI detection
├── Pass 1: Build per-cell baseline (mean, std) across all scenes
│   ├── Scene-level median normalization (removes incidence angle effects)
│   ├── DEM terrain masking (Copernicus 30m, slope >15°)
│   └── Geographic grid accumulation (0.01° resolution)
└── Pass 2: Score each scene against baseline
    ├── Per-pixel z-scores against temporal mean
    ├── Flag z > 3.0 as RFI candidates
    └── Extract point coordinates for mapping
```

### Single-Scene Method (Original)

The original four-method approach (azimuth-line analysis, bright pixel detection, spectral peak analysis, streak detection) is still available in `sentinel1_rfi_demo.py` and `run_jamertest.py`. It works for quick assessment but produces inflated scores in mountainous terrain and is sensitive to orbit geometry.

## Scripts

| Script | Description |
|--------|-------------|
| **Core Pipeline** | |
| `rfi_pipeline.py` | Shared RFI detection module with DEM terrain masking |
| `temporal_rfi.py` | Temporal z-score RFI detection (Iran/Gulf) |
| `temporal_rfi_norway.py` | Temporal z-score analysis for Jammertest Norway |
| **Download & Process** | |
| `iran_download_process.py` | Download and process S1 scenes over Iran |
| `gulf_download_process.py` | Download and process S1 scenes over Persian Gulf |
| `download_iran.py` | Batch download Iran-wide scenes from catalog |
| `check_tehran.py` | Poll CDSE for new S1 passes over Tehran |
| **Map Generation** | |
| `create_iran_map.py` | Interactive Leaflet map for Iran RFI (temporal z-score) |
| `create_gulf_map.py` | Interactive Leaflet map for Persian Gulf RFI |
| `create_norway_map.py` | Interactive map for Jammertest with jammer site markers |
| `create_map.py` | Original Tehran RFI map with SAR overlays |
| **Analysis** | |
| `rfi_spatial_norway.py` | Spatial RFI analysis with distance-to-jammer metrics |
| `run_jamertest.py` | Original single-scene Jammertest analysis |
| `run_lacourtine.py` | France La Courtine GNSS jamming test analysis |
| `sentinel1_rfi_demo.py` | Original core pipeline (single-scene method) |

## Quickstart

### Prerequisites

- Python 3.9+
- Free [Copernicus Data Space](https://dataspace.copernicus.eu) account

### Install

```bash
git clone https://github.com/zephr-xyz/sentinel1-rfi-detection.git
cd sentinel1-rfi-detection
pip install -r requirements.txt
```

### Configure credentials

```bash
cp .env.example .env
# Edit .env with your CDSE credentials
```

### Run

```bash
# Temporal z-score analysis (recommended)
python temporal_rfi.py              # Iran
python temporal_rfi.py gulf         # Persian Gulf
python temporal_rfi_norway.py       # Jammertest Norway

# Generate interactive maps
python create_iran_map.py
python create_gulf_map.py
python create_norway_map.py

# Original single-scene pipeline
python sentinel1_rfi_demo.py --demo     # Synthetic data demo
python sentinel1_rfi_demo.py            # Full pipeline (Tehran)
python run_jamertest.py                 # Norway Jammertest (original method)
```

## Map Output

The map generators produce self-contained HTML files with:
- Leaflet.js with satellite/dark basemaps and label overlays
- Zoom-adaptive grid cells (0.5° at zoom 5 down to 0.001° at zoom 13+)
- Density-based coloring: top 5% = RED/HIGH, top 15% = ORANGE/MODERATE, rest = GREEN/LOW
- Per-cell RFI score (0–100) in click popups
- Time slider for temporal animation across acquisition dates
- Jammer site markers with range rings (Norway map)

## Key Findings

1. **Terrain masking is essential.** Mountains produce severe false positives in SAR RFI detection due to layover and foreshortening. DEM-based slope masking eliminates 5–22% of pixels per scene depending on topography.

2. **Temporal baselines dramatically reduce false positives.** Single-scene scores of 78–100 drop to 0–1.1 with temporal normalization. The remaining detections are genuine temporal anomalies.

3. **Spatial analysis reveals what scene-level scores hide.** The Jammertest data shows no significant scene-level temporal anomaly, but 10–14x spatial concentration of RFI near the jammer sites — a signal invisible to aggregate scoring.

4. **The L-band to C-band detection question remains open.** The spatial pattern near Jammertest sites is consistent with equipment warm-up emissions from a 50W amplifier, but we cannot definitively rule out co-located RF sources. A coordinated experiment with jamming during an S1 overpass would be conclusive.

## Data Sources

- **Sentinel-1 GRD** from [Copernicus Data Space Ecosystem](https://dataspace.copernicus.eu)
- **Copernicus DEM 30m** from [AWS S3](https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/)
- **Jammertest schedule** from [NPRA/jammertest-plan](https://github.com/NPRA/jammertest-plan)
- **Basemaps**: [CARTO Dark](https://carto.com/basemaps/), [Esri World Imagery](https://www.arcgis.com/)

## References

- ESA. [Sentinel-1 SAR User Guide](https://sentinels.copernicus.eu/web/sentinel/user-guides/sentinel-1-sar). European Space Agency.
- Recchia, A., et al. (2017). "Impact of Radio Frequency Interference on Sentinel-1 SAR data." *ESA Living Planet Symposium*.
- Meyer, F.J., et al. (2013). "Mapping GPS interference in Alaska using Sentinel-1." *IEEE Geoscience and Remote Sensing Letters*.
- Tao, M., et al. (2019). "Radio Frequency Interference Detection and Mitigation for Sentinel-1." *IEEE Trans. Geoscience and Remote Sensing*.
- Norwegian Communications Authority. [Jammertest](https://www.jamertest.no/). Annual GNSS jamming and spoofing test event.

## License

MIT — see [LICENSE](LICENSE).
