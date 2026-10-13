# M5 explainer: our own flood segmentation model (the AI component)

Each part below covers **what**, **why**, **what if removed** and **what could replace it**.
Numbers marked *(measured)* come from the training run and the pipeline runs (section 7).

---

## 1. Why this AI component (owner decision 2026-10-13)
- **What the brief allows:** both PDFs say: *"AI COMPONENT — Choose one of the following"*
  (25 % of judging). The two options are:
  1. *Flood segmentation model:* "Train a model to detect water or debris in satellite
     images using the listed training data, and show how well it works on Himalayan scenes
     it has never seen."
  2. *Situation-report copilot* built on a language model.
- **Why option 1:**
  - **No external AI service and no data leaving the machine.** Option 2 would need an
    outside language-model API, or someone else's downloaded model.
  - It is **our own model**, trained from scratch, which the owner can explain end to end.
  - It runs offline in the live demo, so no internet, API key or quota can fail.
- **The one-page sit-rep stays** (brief "OUTPUT"). It becomes a code-filled template with
  no language model (M6).

## 2. Training data (`scripts/fetch_sen1floods11.py`)
- **What we use:** Sen1Floods11, listed in the brief as training data (optional), from its
  official bucket. Only the hand-labelled Sentinel-1 chips (`S1Hand`, VV + VH in dB), their
  water labels (`LabelHand`) and the official split lists. That is 896 files, 0.73 GB, each
  checked against the bucket's MD5.
- **What we refuse:** the bucket also holds a `JRCWaterHand` folder. JRC surface water is a
  forbidden dataset, so `data_rules.check_training_object` refuses it and a test proves it.
  The optical chips (`S2Hand`) are not used either: radar works through monsoon cloud.
- **Coverage:** 446 chips (512×512 px at 10 m) from 11 flood events: Bolivia, Ghana, India,
  the Mekong region, Nigeria, Pakistan, Paraguay, Somalia, Spain, Sri Lanka and the USA.
  **No Nepal or Himalayan event**, so Trishuli, Melamchi and Sikkim are truly unseen.
- **Official splits:**
  - train: 252 chips;
  - validation: 89 (used to pick the best model);
  - test: 90;
  - **Bolivia:** 15 chips from an entire flood event that the dataset's authors kept out
    of training. This is our "unseen event" check.
- **Why not Kuro Siwo** (the "recommended" set): it is hosted on Dropbox and Hugging Face
  and is far too large for the time and laptop we have. Sen1Floods11 is equally on the
  brief's list.
- **Licence and citation:** "fine for non-commercial educational use"; we cite Bonafilia et
  al., CVPR Workshops 2020 in every output where the model appears (PNG footer,
  results.json `training_data_citations`, model card).

## 3. The model (`scripts/train_segmentation.py`)
- **Architecture:** a small **U-Net**, 3 levels, 16→128 channels, **482,593 parameters**.
  Input: 2 channels (VV, VH). Output: one water logit per pixel.
  - *Why a U-Net:* radar images are speckled. A convolutional network judges each pixel
    from its neighbourhood, so it ignores isolated dark specks that a per-pixel threshold
    flags.
  - *Why small:* it trains on a laptop CPU (Intel i3, 2 cores, no GPU) in about an hour,
    and runs in seconds.
- **Trained from scratch:** no pretrained weights, because Discord question 4 on whether
  they are allowed is unanswered. This is also simply "our own model".
- **Resolution:** chips are averaged from 10 m to **20 m**, the pipeline's resolution.
  Backscatter is averaged in linear power, not dB. A label pixel is water if most of its
  valid 10 m sub-pixels are water.
- **Preprocessing:** dB clipped to −30…0 and scaled to 0…1. It is defined once in
  `floodmap/segmentation.py` and imported by the training script, so training and the
  pipeline can never prepare images differently.
- **Training setup:**
  - loss: binary cross-entropy + Dice on valid pixels;
  - augmentation: random 90° rotations and flips;
  - optimiser: AdamW with a one-cycle learning-rate schedule;
  - 30 epochs (passes over the training data), fixed seed;
  - the epoch with the best validation IoU is kept.
- **Export:** saved as ONNX (`models/segmentation/s1_water_unet.onnx`, a few MB). The
  pipeline runs it with **onnxruntime**: no PyTorch needed for judges, CI or the demo.
  PyTorch is only in the `train` extra (`requirements-train.txt`).
- **Timing** *(measured)*: 109 s per epoch on the owner's laptop.

## 4. Use in the pipeline (`stages/segmentation.py`)
- **Steps:**
  1. The model runs on the primary track's **before** and **after** images (any area size:
     overlapping 512 px tiles, only tile centres kept).
  2. Water before / water after are computed, and **new water** = water after, not before.
  3. New water is kept only inside the terrain-plausible zone and where the radar geometry
     is valid, the same rules as the change detection, with the same minimum patch size.
- **Not counted as an extra "source" in the confidence map:**
  - The model reads the *same* Sentinel-1 image as the change detection. Their agreement is
    not independent evidence, and counting it would inflate "high confidence".
  - Instead the stage reports its **agreement** with the change detection, and M7 compares
    it with the official reference maps (checking only).
- **Missing model file:** the stage is skipped with a clear message; the rest of the
  pipeline is unaffected.

## 5. How well does it work? (the brief's question)
1. **Sen1Floods11 held-out test chips and the unseen Bolivia event:** IoU, precision,
   recall and F1 against the hand labels. Compared with the simple rule a non-AI system
   uses ("water where VV ≤ −15 dB", our pipeline's water rule) *(section 7)*.
2. **Himalayan scenes it has never seen** (Trishuli, Melamchi, Sikkim): agreement with our
   change detection, plus the comparison with the official reference maps in M7.

## 6. Limitations (honest)
- **Domain shift:** the training chips are mostly lowland floodplains processed by another
  pipeline. Our images are Copernicus Data Space terrain-corrected backscatter in narrow
  mountain valleys with layover and shadow. Expect lower accuracy in the Himalaya than on
  the test chips.
- **Water only:** Sen1Floods11 labels water, not debris or mud. The model cannot map debris
  fields, which the change detection still covers.
- **Small and CPU-trained:** a larger model or more data (Kuro Siwo) would likely do
  better.
- **Seasonal water:** water before vs after is mapped per image, so monsoon-filled paddies
  can still look like new water.

## 7. Evidence (measured 2026-10-13)
**Training** (`models/segmentation/model_card.json`):
- 30 epochs, 40.8 min on the laptop CPU; best validation IoU at epoch 25.
- Model file 1.9 MB (ONNX).
- Fixed seed: a second run reproduced the first exactly, loss for loss. The first run's
  model was lost because the exporter needed the `onnx` package; the script now saves a
  checkpoint first and checks the export in its first seconds.

**Held-out accuracy (water / not water, 20 m pixels):** model vs the simple threshold rule.
Each cell is IoU / precision / recall / F1.

| Data | Our U-Net | Threshold rule (VV ≤ −15 dB) |
|---|---|---|
| Sen1Floods11 test chips (90) | **0.649** / 0.760 / 0.816 / **0.787** | 0.445 / 0.673 / 0.568 / 0.616 |
| **Bolivia: entire flood event never seen (15 chips)** | **0.648** / 0.785 / 0.787 / **0.786** | 0.581 / 0.873 / 0.635 / 0.735 |
| validation chips (89, used to pick the epoch) | **0.629** / 0.753 / 0.793 / **0.772** | 0.447 / 0.655 / 0.584 / 0.618 |

- The model beats the simple rule on every split.
- On a **flood event it never saw** (Bolivia) it keeps its accuracy (IoU 0.648 vs 0.649 on
  the test chips). The threshold rule reaches 0.581 there.

**Can the existing model be improved? (owner request "improve the existing model"; measured
2026-10-13; record: `models/segmentation/experiments.json`)**
- The training IoU (0.583) is below validation (0.629), which looked like slight underfitting.
  Three improvements were tried with the same data, splits, seed and script.
- **Rule fixed before training:** adopt a candidate only if its **validation** IoU beats the
  current model by at least 0.005 (smaller differences are noise). Test and Bolivia are
  reported once and never used to choose, so they stay honest held-out numbers.

| Variant | What changes | Validation IoU (decides) | Test IoU | Bolivia IoU |
|---|---|---|---|---|
| **Current** | base 16, 30 epochs | **0.6293** | 0.649 | **0.648** |
| Bigger | 4× parameters (base 32), 50 epochs: the classic cure for underfitting | 0.6285 | 0.658 | 0.536 |
| V1 | weight averaging (EMA of the weights, warm-up) | 0.6247 | 0.652 | 0.585 |
| V2 | V1 + random brightness shifts ±1.5 dB (sensor/region differences) | 0.6242 | 0.654 | 0.590 |
| Ensemble | average of current, V1, V2 (no training; `scripts/eval_ensemble.py`) | 0.6281 | 0.654 | 0.607 |

- **Decision: keep the current model.** No candidate passes the rule.
- **What this shows:**
  - More capacity did not help, so the gap is not underfitting from model size. The limit is
    the information in one two-band radar image: calm water, smooth sand, tarmac and radar
    shadow all look dark.
  - Test IoU moves only between 0.649 and 0.658 across all four: the model is stable.
  - **Bolivia (15 chips) varies a lot between trainings (0.536–0.648).** Our 0.648 is likely
    at the favourable end; a fair statement is "about 0.54–0.65 on an unseen event, against
    0.58 for the threshold rule".
- **Bug caught on the way:** the first weight-averaging run scored 0 because the average still
  held ~24 % of the random starting weights at epoch 9. Fixed with the standard warm-up; the
  run was restarted, not patched.
- Candidates stay out of the repository (`models/segmentation/candidates/` is git-ignored).

**Unseen Himalayan scenes** (full pipeline; nothing from these areas was in training):

| Scene | Model new water in plausible zone | Model "water" outside the zone (radar shadow) | IoU with change-detection water | Change detection (all classes) |
|---|---|---|---|---|
| Trishuli 2026-08-26 | 0.89 km² | 21.55 km² | 0.10 | 15.60 km² |
| Melamchi 2021-06-15 | 0.08 km² | 0.03 km² | 0.04 | 3.57 km² |
| Sikkim 2023-10-04 | 0.01 km² | 10.43 km² | 0.01 | 2.06 km² |
| No-flood control 2026-02-15 | **0.00 km²** | 2.69 km² | — | 0.09 km² |

- **What this shows (honest answer to the brief's question):** the model transfers poorly
  to Himalayan gorges.
  1. It **mistakes radar shadow on steep slopes for water**. The terrain masks remove this,
     which is why it is reported separately and not counted.
  2. It **misses the narrow river corridor**. The debris flood shows as a band 1–5 pixels
     wide, mostly *wet mud / debris* change rather than open water, and the training data
     has only wide lowland water.
- **The good part:** it raises **no false new water** in the no-flood control.
- **Consequence:** the change detection (M1–M2) stays the primary flood map. The model is a
  documented, independent second look, and its weakness in mountains is a finding for the
  report. M7 will measure it against the official reference maps.
- **Possible improvements** (not done; time and the data rules): train on Himalayan
  examples (none are allowed as labels: published damage maps are checking-only), add
  terrain inputs, or use Kuro Siwo's pre/post pairs.
