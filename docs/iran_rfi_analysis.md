# When the Planes Disappear, the Satellites Still See

We previously examined how the Iranian regime has used GPS jamming as a pressure point against Starlink. With the latest round of strikes inside Iran, I wanted to understand something more fundamental: are those domestic jamming operations still functioning under stress?

Most public jamming analytics, including GPSjam.org, rely heavily on ADS-B data from aircraft. When the airspace over Iran was abruptly cleared, one of the primary open-source signals for mapping interference disappeared overnight. The fog of war did not just obscure the battlefield. It removed the telemetry.

That gap created an opportunity to test a different approach I have been wanting to explore. Instead of depending on aviation-based signals, I used synthetic aperture radar to detect radio frequency interference patterns consistent with GPS jamming activity.

Iran's ability to sustain interference against systems like Starlink may be more than a technical footnote. It could serve as a real-time indicator of the regime's capacity to project control and suppress dissent under pressure.

The results are in this post.

## Why SAR Can See What ADS-B Cannot

The core idea is deceptively simple. Sentinel-1, the European Space Agency's synthetic aperture radar satellite, operates in C-band -- roughly 5.4 GHz. GPS jammers, particularly the kind Iran has deployed domestically, tend to be broadband emitters. They do not confine their energy neatly to the GPS L1/L2 frequencies. They bleed across the spectrum, and when that interference reaches the same frequency neighborhood as a SAR instrument, it leaves a signature in the radar data.

This is not a new observation. ESA has documented radio frequency interference artifacts in Sentinel-1 imagery for years, and researchers have used these patterns to map jamming hotspots across conflict zones. But most of that work has been retrospective -- academic papers published months after the fact. What I wanted to know was whether you could build a near-real-time detection pipeline that runs automatically as new imagery becomes available.

The answer is yes, and it is not particularly complicated.

## The Detection Model

Sentinel-1 collects data in Interferometric Wide Swath (IW) mode over Iran, producing Ground Range Detected (GRD) images at roughly 10-meter resolution. Each scene covers approximately 250 km x 170 km -- a single pass over Tehran captures everything from the Alborz Mountains to the southern edge of the city.

Each product comes with two polarizations: VV (vertical transmit, vertical receive) and VH (vertical transmit, horizontal receive). RFI tends to show up in both, but the cross-pol VH channel is particularly revealing because it normally has lower backscatter. Interference stands out against a quieter background.

The detection pipeline works in three steps:

1) **Intensity-to-dB conversion and baseline estimation.** The raw pixel values are converted to decibels. A median-filter baseline is computed at reduced resolution to capture the natural backscatter gradient across the scene -- urban areas are brighter than desert, mountains have different returns than plains. The baseline gets upsampled back to the full image grid.

2) **Bright pixel detection.** Subtracting the baseline from the observed data produces a residual. Pixels that exceed 4 standard deviations above the median residual are flagged as anomalously bright. The standard deviation is estimated using the median absolute deviation (MAD), which is robust to the very outliers we are trying to detect. This is the core RFI indicator -- ground-based transmitters produce localized intensity spikes that are physically inconsistent with natural radar returns.

3) **Azimuth line analysis.** RFI from a ground-based jammer illuminates the satellite continuously as it passes overhead, producing characteristic bright streaks along the azimuth (flight) direction. The pipeline computes per-line intensity statistics and flags lines where the mean power exceeds 3 standard deviations above the scene average. A high percentage of flagged azimuth lines is a strong indicator of coherent, sustained jamming rather than transient interference.

These three measurements -- bright pixel percentage, azimuth line contamination, and spectral anomalies -- are combined into a single RFI score on a 0-100 scale.

## Georeferencing Without a Map

One of the more interesting engineering challenges was that Sentinel-1 GRD products do not come with an embedded coordinate reference system in the GeoTIFF. The pixels are in sensor coordinates -- a grid defined by radar range and azimuth timing. The geographic information lives in the annotation XML files as a 210-point control grid that maps (line, pixel) positions to (latitude, longitude).

To produce a map, you need to warp the sensor data into geographic coordinates. I built a forward interpolation model using those 210 control points with cubic spline fitting, then computed the inverse mapping -- for every point on the output lat/lon grid, where does it fall in sensor space? -- using scipy's griddata. That inverse mapping feeds into a resampling step that pulls pixel values from the original SAR image into their correct geographic positions.

The same warping pipeline transforms the RFI bright-pixel masks into geographic coordinates, where they get vectorized into GeoJSON polygons using rasterio and shapely. Those polygons carry metadata -- the product name, date, polarization, RFI score, and severity classification -- and become the clickable features on the interactive map.

## What the Data Shows

I started with two Sentinel-1A passes over Tehran from February 18 and 19, 2026 -- an ascending orbit and a descending orbit captured roughly 12 hours apart. Both showed moderate RFI contamination. The February 19 VH channel scored 51.8 out of 100 with over 9% of azimuth lines flagged. The VV channel on the same pass scored 38.2. The February 18 descending pass showed similar patterns.

Moderate is not subtle. A score of 50 means the jammer is clearly active and depositing enough energy into the SAR data to be unambiguously detected. It is consistent with a ground-based broadband jammer operating continuously during the satellite overpass.

To put the Tehran observations in a broader context, I expanded the search area to all of Iran for the February 27 through March 1 window. Sentinel-1 does not revisit the same ground track every day -- S1A has a 12-day exact repeat cycle, and Sentinel-1C (which became operational in late 2025) covers complementary orbit tracks. Between the two satellites, I found 11 scenes spanning the country during that three-day window, covering everything from the western border near Kurdistan to the eastern provinces near Afghanistan and from the Persian Gulf coast to the Caspian.

The Iran-wide analysis revealed that RFI contamination is not confined to Tehran. Multiple scenes across different orbit tracks and dates show moderate detection scores, suggesting a distributed network of jamming infrastructure rather than a single point source in the capital. The pattern is consistent with what you would expect from a regime maintaining operational electronic warfare capabilities across its territory.

## The Interactive Map

The output of the pipeline is a single self-contained HTML file -- no server, no API keys, no dependencies. Open it in a browser and you get a Leaflet slippy map centered on Iran with a CARTO Voyager basemap (English labels). Each of the 13 products produces two overlays: the SAR imagery itself, rendered as a semi-transparent grayscale layer, and the RFI detection polygons, styled in red with opacity proportional to severity.

A layer control panel lets you toggle any combination of the 26 overlays on and off. Click an RFI polygon and you get the product name, acquisition date, polarization, RFI score, and severity classification. The entire thing is about 25 MB -- large for a webpage, small for what it contains.

## Near-Real-Time Monitoring

The detection pipeline runs end to end in under two minutes on a laptop. The bottleneck is not computation but data availability -- the Copernicus Data Space Ecosystem (CDSE) typically publishes new products within hours of acquisition, but the catalog lag can vary.

I set up an automated poller that queries the CDSE catalog four times daily for new Sentinel-1 GRD products intersecting Tehran. When a new product appears, it automatically downloads, runs the RFI detection, and regenerates the map. The next Tehran overpass is expected around March 2-3 based on the 12-day repeat cycle.

This is the part that matters for the original question. ADS-B telemetry disappears when Iran clears its airspace. SAR does not care about airspace closures. Sentinel-1 orbits at 693 km altitude and collects data regardless of what is happening on the ground. As long as the satellite keeps flying, we keep seeing.

## What This Tells Us

The jamming infrastructure is active. Not at the highest levels the detection model can measure, but solidly in the moderate range -- consistent with sustained, operational jamming rather than sporadic testing. The geographic spread across Iran suggests this is not a localized defensive measure around a single installation. It looks like a maintained capability.

Whether that capability is sufficient to meaningfully degrade Starlink terminals is a different question, one that requires ground-truth data I do not have. But the fact that the jamming persists under the current conditions -- when you might expect the regime to either escalate electronic warfare or pull resources toward other priorities -- says something about how embedded these systems are in Iran's security posture.

The next Sentinel-1A pass over Tehran should arrive in the first few days of March. The poller is running. Stay tuned.
