# Can Sentinel-1 Detect GNSS Jamming? New Evidence from Temporal Baseline Analysis

We previously investigated whether Sentinel-1 C-band SAR (5.405 GHz) could detect radio frequency interference from GNSS jammers operating at L-band (~1.2-1.6 GHz), using Norway's annual Jammertest exercise as ground truth. Our initial single-scene analysis produced inflated scores and a misleading correlation with the test week. We called it a textbook MAUP problem and moved on.

We were half right. The MAUP critique stands — the original aggregate scoring was genuinely misleading. But after rebuilding the detection pipeline with temporal baseline z-scores and DEM terrain masking, then cross-referencing the spatial results against the three known jammer sites, the picture is more nuanced than "no detection." There is a spatially concentrated RFI anomaly near the jammer cluster that demands a more careful explanation.

## The Setup

Norway's NPRA runs Jammertest at three sites on northern Andøya: Bleik (meaconing/spoofing antennas), Ramnan (the 50W "Porcus Maior" PRN jammer), and Stave (Site 3, additional test equipment). The sites form a ~5km cluster. NPRA publishes the full transmission schedule on GitHub (github.com/NPRA/jammertest-plan), including equipment specs, power levels, and minute-level daily plans. The 2025 test ran September 15-19 with all equipment operating at L-band GNSS frequencies (1,176-1,621 MHz).

We analyzed six Sentinel-1 scenes over Andøya: two pre-event baselines, two test-week passes, one late-event pass, and one post-event baseline.

## What Changed: Temporal Z-Score Detection

The original analysis used single-scene statistical thresholds — median absolute deviation to flag bright pixels, azimuth-line means to flag RFI streaks, spectral peak counts. These methods are sensitive to terrain, incidence angle variation, and the particular orbit geometry of each scene. Mountains produce false positives. Different orbit tracks produce incomparable scores.

The new pipeline addresses this with a two-pass temporal approach:

**Pass 1** builds a per-location baseline by accumulating every scene's radar backscatter (in dB, normalized by scene median) onto a geographic grid. Each scene is normalized against its own median to remove orbit-dependent incidence angle effects. Copernicus 30m DEM data masks steep terrain (slope >15 degrees) before accumulation, eliminating the layover/foreshortening artifacts that plagued the original analysis.

**Pass 2** scores each scene against the baseline. For every pixel, we compute a z-score: how many standard deviations is this observation from the temporal mean at this geographic location? Pixels with z > 3.0 are flagged as RFI candidates. The result is a detection that fires only on genuine temporal anomalies — terrain, stable urban backscatter, and orbit geometry effects are all absorbed into the baseline.

Scenes are grouped by orbit direction (ascending vs. descending) since the viewing geometries are too different to form a common baseline. With 4 descending and 2 ascending scenes, the baselines are thin but functional.

## The Scores Come Down — But the Spatial Pattern Doesn't

The temporal model produces dramatically lower scene-level scores than the original analysis:

| Date | Sat | Dir | Local Time | Old Score | New Score | Context |
|------|-----|-----|-----------|-----------|-----------|---------|
| Sep 10 | S1A | DESC | 18:15 | 30 | 0.0 | Pre-event baseline |
| Sep 11 | S1C | DESC | 18:07 | 100 | 1.1 | 4 days before test week |
| Sep 16 | S1A | ASC | 07:45 | 78 | 0.6 | Test week morning |
| Sep 16 | S1C | DESC | 18:15 | 100 | 0.6 | Test week evening |
| Sep 18 | S1A | ASC | 07:29 | 36 | 0.0 | Test week morning |
| Sep 20 | S1A | DESC | 18:32 | 38 | 0.0 | Post-event baseline |

The old VH=100 scores were artifacts — driven largely by terrain effects in northern Norway's fjord-and-mountain topography, plus the spectral peak component that saturated at 30 points for any scene with moderate backscatter variation. With terrain masking and temporal normalization, those false positives disappear.

But the spatial concentration near the jammer sites tells a different story:

| Date | Context | % RFI <10km | % RFI <20km | Min Dist | Ramnan <10km |
|------|---------|------------|------------|----------|-------------|
| Sep 10 | **Baseline** | 0.1% | 0.2% | 3.3 km | 0.1% |
| Sep 16 DESC | **Test week evening** | **3.4%** | **7.6%** | **0.3 km** | **3.1%** |
| Sep 16 ASC | **Test week morning** | **3.8%** | **8.0%** | **0.4 km** | **3.4%** |
| Sep 20 | **Baseline** | 0.4% | 1.3% | 2.0 km | 0.4% |

The Sep 16 scenes show **10x more RFI within 20km of the jammer cluster** than baselines, and **14x more within 10km**. This is consistent across both the morning ascending pass and the evening descending pass — two completely independent orbits, viewing geometries, and times of day. The concentration is tightest around Ramnan, the Porcus Maior site.

## The Timing Problem Remains — But Narrows

Cross-referencing the NPRA daily schedule for September 16 against the S1 overpass times:

**Morning pass (S1A, 07:45 local):** The mandatory morning brief starts at 08:00. First scheduled transmission at 09:00. The S1 overpass is 1 hour 15 minutes before any planned jamming.

**Evening pass (S1C, 18:15 local):** The day's main sessions ended around 18:00. At 18:15, only test 0.0.2 was active — a 0W site closedown procedure. The 50W Porcus Maior high-power session (test 1.16.5) was scheduled to begin at 19:00 — just 45 minutes after the S1 overpass.

No S1 pass coincided with a scheduled transmission. That hasn't changed.

## The Warm-Up Hypothesis

But "scheduled transmission" and "equipment powered on" are not the same thing. Consider the evening pass:

The 50W Porcus Maior amplifier chain at Ramnan was scheduled to begin a 3-hour high-power test at 19:00 local. Standard RF procedure for a 50W power amplifier involves powering up the system, running calibration sequences, and verifying output before the test window opens. At 18:15 — 45 minutes before the scheduled start — equipment would plausibly be in warm-up or pre-test calibration.

Power amplifiers in warm-up produce more spurious out-of-band emissions than when operating at their design point. A 50W (+47 dBm) broadband amplifier approaching saturation could generate intermodulation products and harmonics well outside its intended L-band operating range. Whether those spurious emissions reach C-band at detectable levels is an open question, but it is not physically impossible — particularly for a wideband jammer design that isn't optimized for spectral cleanliness.

The morning pass presents a weaker but still plausible version of the same hypothesis. Personnel arrive at 08:00. Equipment checks before a full day of testing could involve powering up and testing equipment 30-60 minutes before the first scheduled transmission.

**What supports the warm-up hypothesis:**
- 14x spatial concentration increase near jammer sites on Sep 16, consistent across two independent orbits
- Tightest concentration at Ramnan — the Porcus Maior (50W) site specifically — with detections 300 meters from the transmitter location
- Sep 18 morning pass (same time window, 07:29 local) does **not** show the same pattern — only 1.0% within 20km. That day's equipment was all low-power (0.001-0.3W), so there would be less to warm up
- The power-level correlation: high spatial anomaly when the 50W system is about to operate, no anomaly when only milliwatt-class equipment is scheduled

**What argues against it:**
- L-band to C-band is a 3.8 GHz gap. Even out-of-band emissions from a 50W amplifier would need to bridge that gap at detectable power levels
- Sep 11 also shows mildly elevated proximity (1.9% <20km) four days before the test week, suggesting some ambient RF source near the sites unrelated to Jammertest equipment
- n=6 scenes is a small sample. The statistical comparison is suggestive but not conclusive

## What Changed From the Original Analysis

The original post concluded that the apparent Jammertest correlation was entirely a MAUP artifact. That conclusion was correct about the scene-level scores — those were inflated by terrain effects and a scoring formula that saturated. The temporal z-score model eliminates those false positives.

But the original spatial analysis measured distance from bright pixels to a single point (Bleik). It found centroids 80-160km east and concluded "nowhere near the jammers." That analysis used the single-scene threshold method, which flags far more terrain-correlated pixels that dilute the spatial signal. The temporal z-score approach, by eliminating stable terrain effects, reveals a tighter spatial concentration that the original method couldn't see.

The corrected picture: the scene-level "RFI scores" during test week were genuinely misleading. But within the smaller population of true temporal anomalies — pixels that are bright relative to their own multi-pass baseline — there is a real spatial clustering near the jammer sites that we can't attribute to terrain, orbit geometry, or the scoring methodology.

## What Would Settle This

The question is no longer "did we see anything?" We saw a spatially concentrated temporal anomaly at the jammer sites. The question is whether that anomaly is caused by L-band equipment producing out-of-band C-band emissions, or by some co-located RF source (military radar, Andøya Space Center infrastructure) that happens to occupy the same geographic area.

To distinguish these hypotheses:

1. **S1 imagery during the 19:00-22:00 high-power window** on Sep 16 would be definitive. If the spatial concentration intensifies during confirmed 50W transmission, the case is strong. Unfortunately, no S1 pass covers that window.

2. **Jammertest 2026 coordination.** If NPRA could schedule a short high-power transmission during an S1 overpass (they occur at predictable times), a single coordinated experiment would resolve the question. A 25-second S1 pass requires only a few minutes of transmission.

3. **ESA RFI annotations.** Sentinel-1 products include machine-generated RFI annotations in the .SAFE metadata (`annotation/rfi/rfi-*.xml`). Comparing ESA's own RFI flags against the jammer site locations would provide an independent check.

4. **Spectrum analyzer data from the test site.** If NPRA or Nkom recorded out-of-band emissions during equipment warm-up, that would directly confirm or deny C-band leakage from the amplifier chain.

## Takeaway

Our original conclusion — that aggregation bias produced a false positive — was correct about the scoring methodology. The inflated scene-level numbers were real artifacts of terrain and a saturating score formula. But the improved temporal baseline analysis reveals something the original method couldn't isolate: a 10-14x spatial concentration of RFI anomalies within 20km of the jammer cluster, present on both Sep 16 passes and absent from baselines.

The most parsimonious explanation is equipment warm-up: the 50W Porcus Maior amplifier at Ramnan, powering up 45-75 minutes before its scheduled test window, producing out-of-band spurious emissions detectable at C-band. This remains a hypothesis — we cannot rule out co-located RF sources — but the spatial and temporal pattern is more consistent with jammer-related activity than with coincidence.

The honest answer is that we went from "definitely not a detection" to "probably not, but we can't rule it out, and there's a specific mechanism that would explain the spatial pattern." That's less satisfying than a clean narrative in either direction, but it's where the data points.

---

Data: Sentinel-1 GRD from Copernicus Data Space Ecosystem. Jammertest schedule from github.com/NPRA/jammertest-plan. Temporal z-score RFI detection with Copernicus 30m DEM terrain masking. Analysis code available on request.

#SAR #Sentinel1 #RFI #GNSS #Jamming #RemoteSensing #MAUP #SignalProcessing #Copernicus #RadarInterference #Jammertest
