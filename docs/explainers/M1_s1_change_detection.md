# M1 explainer — Sentinel-1 change detection ("Where did the flood hit?")

For each part: **what**, **why**, **what if removed**, **what could replace it**.
"Judge may ask" lines are Q&A practice. All numbers below were measured (2026-10-11);
see section 9.

---

## 1. The idea in one paragraph
A radar satellite (Sentinel-1) sends microwave pulses and measures how much energy comes
back ("backscatter"). Smooth surfaces such as calm water reflect the pulse away like a
mirror, so they look **dark**. Rough surfaces such as boulders, debris and wreckage scatter
it back, so they look **bright**. Radar sees through monsoon clouds. We compare an image
from before the flood with one after it, pixel by pixel. Where backscatter dropped
strongly, something got smoother (new water, wet mud). Where it rose strongly, something
got rougher (debris).

## 2. Pairing: which images to compare (`s1_pairing.py`)
- **Same track only.** A *track* = relative orbit number + pass direction (ascending =
  evening pass going north, descending = morning pass going south). Images from different
  tracks view the mountains from different angles: the slopes facing the satellite,
  shadows and layover all move, so comparing them would show "change" everywhere. The
  brief's tip says the same.
- **Steps:**
  1. Search the public CDSE catalogue for all Sentinel-1 scenes around the date.
  2. Merge the ~25 s slices of one pass into one acquisition.
  3. Per track: *after* = earliest acquisition after the event date; *before* = the latest
     up to 3 acquisitions before it.
  4. Drop images covering < 50 % of the area.
  5. Rank tracks: coverage, then fewest days after the event, then shortest gap between
     the images.
- **Event-day images are excluded** by default. A pass on 26 Aug may have happened before
  the collapse, which would hide the flood (configurable).
- **Not hard-coded to 12 days.** The rule is "same track". With two satellites the gap can
  be 6 days (tested: S1A + S1D on one track pair correctly).
- **If removed / done naively** (e.g. "nearest image before and after"): we would often
  pair images from different tracks, and the change map becomes noise.
- **Trishuli result:** 3 tracks have before + after images. Chosen: **track 85 ascending**,
  before = 23 Jul, 4 Aug, 16 Aug, after = **28 Aug (2 days after)**. Alternatives: track
  121 descending (31 Aug), track 19 descending (5 Sep).
- *Judge may ask:* "Why not use the image closest in time?" → It may come from another
  orbit track; different geometry = false change. We pair within a track and choose the
  track with the earliest post-event image.

## 3. Getting calibrated images from Copernicus Data Space (`s1_change.py`)
- **Source:** CDSE openEO, collection `SENTINEL1_GRD`: the source named in the brief.
- **`sar_backscatter`** converts raw GRD values into calibrated backscatter (σ⁰) and
  geocodes it with **terrain correction using the Copernicus DEM (`COPERNICUS_30`)**, an
  allowed input. In mountains this places every pixel where it really is on the ground.
- **One request per area:** CDSE loads only the chosen track (relative-orbit + pass
  filter), takes the per-pixel **median of the before images** and returns before + after
  as one 4-band GeoTIFF (`pre_VV, pre_VH, post_VV, post_VH`). Measured: **105 s** for a
  ~110 km² test area.
- **Fallback:** if that request fails, one request per image, one at a time. Each file is
  written under a temporary name and renamed only when complete, so a crash can never
  leave a half file that looks finished, and a retry resumes where it stopped.
- **CDSE facts we learned (and handle):**
  - The free tier rejects a second simultaneous request (HTTP 429). We send one at a time
    and wait + retry on 429.
  - The time axis is labelled per day, so time filters use whole days. That is safe
    because the orbit filter already isolates one track.
  - The local-incidence-angle output is not supported; we will compute slope and
    layover/shadow ourselves from the Copernicus DEM in M2.
  - Timestamps must be `YYYY-MM-DDTHH:MM:SSZ`.
- **20 m pixels:** Sentinel-1 IW GRD has ~20 m true resolution (the 10 m product is
  oversampled), so 20 m loses little detail and quarters data volume. Very large areas
  are automatically processed coarser (`max_pixels`).
- **Cache:** downloads are cached under a key of AOI + date + exact scene ids + resolution
  + processing. The first Trishuli run took 238 s; the cached re-run took **15 s** with
  identical numbers.

## 4. Local processing (numpy, deterministic, tested offline)
1. **Speckle filter.** A 3×3 median in linear power (60 m at 20 m pixels). Speckle is the
   grainy interference noise of radar. A median keeps river banks sharp. Larger windows
   would blur rivers that are only 50–150 m wide.
2. **Median of 3 before images** (done on CDSE). It suppresses speckle and one-off effects
   such as rain or wet snow on one date.
3. **dB and log-ratio:** LR = 10·log10(after / before). A ratio, not a difference, so the
   same physical change gives the same LR on bright and dark slopes.
4. **Validity:** pixels below −30 dB (image borders, radar shadow) are no-data.

## 5. Classification (`thresholds.py`)
| Class | Rule | Physical meaning |
|---|---|---|
| new water-like | LR_VV ≤ −3 dB **and** after-VV ≤ −15 dB | got much smoother **and** is now dark like open water |
| strong decrease | LR_VV ≤ −3 dB, not that dark | wet mud, wet debris, damaged vegetation |
| strong increase | LR_VV ≥ +3 dB or LR_VH ≥ +3 dB | boulders, debris, wreckage |
| no change | otherwise | |

- **Why ±3 dB:** a factor of 2 in backscatter, far above the speckle noise left after
  filtering (the log-ratio spread is ~1.4 dB). It is a common, conservative choice and
  behaves predictably on unseen areas.
- **Why water must also be dark:** a drop can happen on wet soil too; open water is
  typically far below −15 dB in VV.
- **Minimum patch 2,000 m² (5 pixels):** single flagged pixels are almost always speckle.
- **Otsu (data-driven) option:** computed per tail of the histogram, used only if the
  tail is clearly two-peaked (separability ≥ 0.5) and within sane bounds, otherwise we
  fall back to ±3 dB. On Trishuli Otsu was **rejected** in both test reaches (the change is
  a small fraction of the image, so the histogram is not two-peaked). This is why fixed
  thresholds are the default.
- **Never tuned against EMSR927.** Thresholds are physical/conventional values; the
  EMSR927 comparison (M7) is an independent check.

## 6. Robustness built into this stage
- No pair → clear message ("No orbit track has both..."), stage FAILED, pipeline continues.
- VV-only images → handled (VH skipped, noted).
- Images partly outside the area → coverage check + "only X % valid" warning.
- Tiny areas → scale bar fixed for maps < 2 km (bug found by a test).
- CDSE busy / errors → retry, fallback, resumable downloads.

## 7. Ablation: what if we remove parts? (measured, lower Trishuli reach)
| Variant (one change vs baseline) | Log-ratio noise (std) | Pixels flagged |
|---|---|---|
| Baseline (3×3 speckle filter, median of 3 before images, VV, ±3 dB) | 1.41 dB | 1.71 % |
| No speckle filter | 2.33 dB | 5.53 % (3.2× more, mostly noise) |
| Single before image | 1.60 dB | 3.24 % (1.9× more) |
| VH instead of VV | 1.54 dB | 2.97 % (but traces the river channel far more continuously) |
| Otsu thresholds | Otsu rejected → ±3 dB | 1.71 % (identical) |

Syabrubesi reach, same pattern: 1.35 / 2.23 / 1.55 / 1.37 dB std and 1.43 / 5.34 / 3.04 /
1.52 % flagged. Figures: `outputs/ablation_s1/*/ablation_s1.png`.
- *Judge may ask:* "Is the speckle filter necessary?" → Without it, 3× more pixels are
  flagged on the same scene, almost all noise.
- *Judge may ask:* "Why three before images?" → With one, 1.9× more false flags; the
  median removes date-specific effects.

## 7b. Robustness runs on events the system has never seen (2026-10-11)
| Run | Pair chosen | Time | Water-like | Strong increase |
|---|---|---|---|---|
| Melamchi flood 2021-06-15 (~180 km²) | track 19 desc, after 06-20 (S1A) | 106 s | 0.09 km² | 21.39 km² |
| Sikkim/Teesta GLOF 2023-10-04 (~280 km²) | track 12 asc, after 10-07 | 98 s | 0.34 km² | 12.26 km² |
| Trishuli dry season 2026-02-15, no flood (~110 km²) | track 19 desc, after 02-18 | 62 s | 0.09 km² | 0.71 km² |
- All three ran end to end with the one-request method; no crash, no fallback needed.
- The **no-flood control stays clean** (< 1 % flagged).
- **Melamchi finding:** the river channel shows a sharp line of decrease (real signal), but
  the whole scene got brighter (median log-ratio **+0.69 dB**). The before images are from
  dry May, the after image from monsoon June; wet soil and vegetation backscatter more.
  This produces scattered false "increase" patches on the slopes.
- **Scene-shift measurement** (median log-ratio, and share of pixels ≥ +3 dB before → after
  removing that median): Trishuli −0.01 dB (1.20 → 1.21 %), Melamchi +0.69 dB
  (6.47 → 2.73 %), Sikkim +0.44 dB (3.27 → 1.68 %), dry control −0.09 dB (0.68 → 0.84 %).
  Removing the shift halves false increases on monsoon-onset scenes but raises flagged
  decreases (Melamchi 0.93 → 2.29 %). It is a trade-off, not a free fix; terrain masks (M2)
  address the slope false positives more directly.

## 7c. Proposals arising from M1 (need author approval)
- **P16 — use VH for decreases too.** In the ablation, VH traced the flooded river channel
  far more continuously than VV. Proposed rule: strong decrease if LR_VV ≤ −3 dB **or**
  LR_VH ≤ −3 dB (water still requires dark VV).
- **P17 — optional scene-shift correction** (subtract the median log-ratio) for scenes
  with a large global shift, evaluated after the M2 terrain masks are in place.

## 8. Known limitations at this stage (honest)
- No terrain masks yet: steep slopes, radar layover/shadow and high ground far from the
  river produce scattered false flags. M2 adds slope, height-above-river (HAND) and
  layover/shadow masks from the Copernicus DEM.
- The river channel is already water before the event, so a flood **inside** the channel
  can show little change. Changes on the banks and floodplain carry the signal.
- 20 m pixels in a 50–150 m wide valley: mixed pixels at the edges.
- Monsoon river changes between dates can look like flood change. M7's EMSR927 comparison
  measures how much of our map is real.
- One track only so far. M2 fuses ascending + descending tracks (track 121 sees the
  valley from the other side, which helps where track 85 is in shadow).

## 9. Evidence (2026-10-11)
- Pair: track 85 ascending, before 2026-07-23 / 08-04 / 08-16, after 2026-08-28,
  Sentinel-1D, VV+VH, 20 m, EPSG:32645.
- Areas inside the 1,791.7 km² AOI: water-like 0.823 km², strong decrease 11.182 km²,
  strong increase 20.012 km² (Sentinel-1 only, unmasked).
- GO/NO-GO (author: **GO**): continuous change along the lower Trishuli channel over
  ~20 km; a chain of debris-type increases along the valley floor near Syabrubesi
  (weaker, narrow steep valley). Figures: `outputs/<run>/s1/zoom_*.png`, `s1_focus.png`.
- Timings: first run 238 s (per-image fallback), cached re-run 15 s; one-request
  download 105 s for ~110 km².
- Tests: pairing (12), thresholds (8), local processing + rendering (8), plus all M0 tests.
