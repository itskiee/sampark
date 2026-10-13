"""Does averaging our trained models (an ensemble) beat the single current model?

No training: the saved ONNX models are run with onnxruntime exactly as in the pipeline, on
the Sen1Floods11 chips at 20 m, and their water probabilities are averaged.
Decision rule (fixed before running, same as every model change): adopt only if the
ensemble's VALIDATION IoU beats the current model by >= 0.005. Test and Bolivia are printed
for the record but never used to decide.

Usage: .venv/Scripts/python scripts/eval_ensemble.py MODEL.onnx [MODEL.onnx ...]
(the first model is the current one)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_segmentation import load_split, scores  # noqa: E402

MIN_GAIN = 0.005


def probabilities(model: Path, x: np.ndarray) -> np.ndarray:
    sess = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name
    out = [sess.run(None, {name: x[i:i + 1]})[0][:, 0] for i in range(len(x))]  # batch 1
    return 1.0 / (1.0 + np.exp(-np.concatenate(out)))


def main() -> int:
    models = [Path(p) for p in sys.argv[1:]]
    if len(models) < 2:
        print(__doc__)
        return 2
    report = {}
    for split in ("valid", "test", "bolivia"):
        x, y, _ = load_split(split)
        probs = [probabilities(m, x) for m in models]
        report[split] = {"current": scores(probs[0] >= 0.5, y)["iou"],
                         "ensemble": scores(np.mean(probs, axis=0) >= 0.5, y)["iou"],
                         "each": [scores(p >= 0.5, y)["iou"] for p in probs]}
        print(split, json.dumps(report[split]), flush=True)
    gain = report["valid"]["ensemble"] - report["valid"]["current"]
    decision = "adopt" if gain >= MIN_GAIN else "keep the current model"
    print(f"validation gain {gain:+.4f} (rule: >= {MIN_GAIN}) -> {decision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
