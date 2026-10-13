"""Our own flood-water segmentation model (the declared AI component, brief option 1).

A small U-Net trained by us, from scratch, on the Sen1Floods11 hand-labelled Sentinel-1
chips (listed training data; scripts/train_segmentation.py). It looks at a radar image
(VV + VH backscatter in dB) and gives every pixel a water probability. Running it on the
pre-event and the post-event image gives NEW water = water after, not before.

Runs fully locally with onnxruntime: no external AI service, no data leaves the machine.
The model file is small (a few MB) and ships with the repository.

Preprocessing lives HERE and is imported by the training script, so training and inference
can never drift apart (a classic silent-failure source).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import numpy as np

from floodmap.config import REPO_ROOT

MODEL_DIR = REPO_ROOT / "models" / "segmentation"
MODEL_PATH = MODEL_DIR / "s1_water_unet.onnx"
CARD_PATH = MODEL_DIR / "model_card.json"

DB_MIN, DB_MAX = -30.0, 0.0  # backscatter range mapped to 0..1 (darker = smoother = water)
TRAIN_RESOLUTION_M = 20.0  # chips are averaged from 10 m to 20 m, the pipeline's resolution
SIZE_MULTIPLE = 8  # three 2x poolings: inputs must be divisible by 8


def normalise(vv_db: np.ndarray, vh_db: np.ndarray) -> np.ndarray:
    """(2, H, W) float32 model input; no-data (NaN) becomes 0 like in training."""
    x = np.stack([vv_db, vh_db]).astype("float32")
    x = np.clip((x - DB_MIN) / (DB_MAX - DB_MIN), 0.0, 1.0)
    x[~np.isfinite(x)] = 0.0
    return x


def predict_tiled(x: np.ndarray, run: Callable[[np.ndarray], np.ndarray], tile: int = 512,
                  overlap: int = 32) -> np.ndarray:
    """Water probability (H, W) for a (2, H, W) input of any size, in overlapping tiles.

    Only the centre of each tile is kept (edges of a convolutional net see less context).
    `run` maps a (1, 2, h, w) batch to (1, 1, h, w) probabilities.
    """
    _, h, w = x.shape
    out = np.zeros((h, w), dtype="float32")
    step = tile - 2 * overlap
    for r0 in range(0, h, step):
        for c0 in range(0, w, step):
            r_lo, c_lo = max(r0 - overlap, 0), max(c0 - overlap, 0)
            r_hi, c_hi = min(r0 + step + overlap, h), min(c0 + step + overlap, w)
            patch = x[:, r_lo:r_hi, c_lo:c_hi]
            ph, pw = patch.shape[1:]
            pad_h, pad_w = (-ph) % SIZE_MULTIPLE, (-pw) % SIZE_MULTIPLE
            padded = np.pad(patch, ((0, 0), (0, pad_h), (0, pad_w)), mode="reflect")
            prob = run(padded[None])[0, 0, :ph, :pw]
            rr, cc = slice(r0 - r_lo, r0 - r_lo + min(step, h - r0)), \
                slice(c0 - c_lo, c0 - c_lo + min(step, w - c0))
            out[r0:r0 + min(step, h - r0), c0:c0 + min(step, w - c0)] = prob[rr, cc]
    return out


class WaterModel:
    """Loads the ONNX model once; `probability(vv_db, vh_db)` -> (H, W) in 0..1."""

    def __init__(self, path: Path = MODEL_PATH, threads: int = 2) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        self.session = ort.InferenceSession(str(path), sess_options=opts,
                                            providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.card = json.loads(CARD_PATH.read_text(encoding="utf-8")) \
            if CARD_PATH.is_file() else {}

    def _run(self, batch: np.ndarray) -> np.ndarray:
        logits = self.session.run(None, {self.input_name: batch.astype("float32")})[0]
        return 1.0 / (1.0 + np.exp(-logits))

    def probability(self, vv_db: np.ndarray, vh_db: np.ndarray) -> np.ndarray:
        prob = predict_tiled(normalise(vv_db, vh_db), self._run)
        prob[~(np.isfinite(vv_db) & np.isfinite(vh_db))] = np.nan
        return prob


def available() -> bool:
    return MODEL_PATH.is_file()
