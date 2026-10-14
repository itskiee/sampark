"""Train OUR flood-water segmentation model (declared AI component) on Sen1Floods11.

Data: Sen1Floods11 hand-labelled Sentinel-1 chips (VV, VH in dB; 446 chips of 512x512 at
10 m) with water / not-water labels, official splits: train 252, valid 89, test 90, plus
Bolivia (15 chips, an entire flood event held out by the dataset authors). No Nepal or
Himalayan event is in the data, so our Himalayan runs are truly unseen scenes.
Cite: Bonafilia et al., CVPR Workshops 2020. Licence: non-commercial educational use.

Model: a small U-Net trained FROM SCRATCH (no pretrained weights), on the laptop CPU.
Chips are averaged to 20 m (the pipeline's resolution). Output: models/segmentation/
s1_water_unet.onnx (runs with onnxruntime) + model_card.json with all metrics.

Usage:
  .venv/Scripts/python scripts/train_segmentation.py --epochs 1 --time-only   # timing test
  .venv/Scripts/python scripts/train_segmentation.py --epochs 40
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import rasterio
import torch
from torch import nn

from sampark.attribution import SEN1FLOODS11_CITATION
from sampark.config import REPO_ROOT
from sampark.segmentation import (
    CARD_PATH,
    DB_MAX,
    DB_MIN,
    MODEL_DIR,
    MODEL_PATH,
    TRAIN_RESOLUTION_M,
    normalise,
)

DATA = REPO_ROOT / "data" / "sen1floods11"
CHIPS = DATA / "data" / "flood_events" / "HandLabeled"
SPLITS = DATA / "splits" / "flood_handlabeled"
SEED = 2026
CHECKPOINT = MODEL_DIR / "checkpoint_best.pt"


# --- data --------------------------------------------------------------------------------
def to_20m(vv_db, vh_db, label):
    """10 m -> 20 m: average backscatter in linear power (not dB); label = majority water
    among the valid sub-pixels, invalid only if all four are invalid."""
    def down(db):
        lin = np.power(10.0, db / 10.0)
        h, w = lin.shape
        blocks = lin[: h // 2 * 2, : w // 2 * 2].reshape(h // 2, 2, w // 2, 2)
        with np.errstate(invalid="ignore", divide="ignore"):
            return 10.0 * np.log10(np.nanmean(blocks, axis=(1, 3)))

    lab = label[: label.shape[0] // 2 * 2, : label.shape[1] // 2 * 2]
    blocks = lab.reshape(lab.shape[0] // 2, 2, lab.shape[1] // 2, 2)
    valid = (blocks >= 0).sum(axis=(1, 3))
    water = (blocks == 1).sum(axis=(1, 3))
    out = np.where(valid == 0, -1, (water * 2 >= valid).astype(np.int16))
    return down(vv_db), down(vh_db), out


def load_split(name: str):
    xs, ys, ids = [], [], []
    with (SPLITS / f"flood_{name}_data.csv").open() as fh:
        for s1_name, label_name in csv.reader(fh):
            with rasterio.open(CHIPS / "S1Hand" / s1_name.strip()) as src:
                vv, vh = src.read(1).astype("float64"), src.read(2).astype("float64")
            with rasterio.open(CHIPS / "LabelHand" / label_name.strip()) as src:
                label = src.read(1)
            vv, vh, label = to_20m(vv, vh, label)
            xs.append(normalise(vv, vh))
            ys.append(label.astype("int64"))
            ids.append(s1_name.split("_S1Hand")[0])
    return np.stack(xs), np.stack(ys), ids


# --- model -------------------------------------------------------------------------------
def block(cin, cout):
    return nn.Sequential(nn.Conv2d(cin, cout, 3, padding=1, bias=False), nn.BatchNorm2d(cout),
                         nn.ReLU(inplace=True),
                         nn.Conv2d(cout, cout, 3, padding=1, bias=False), nn.BatchNorm2d(cout),
                         nn.ReLU(inplace=True))


class UNet(nn.Module):
    """3-level U-Net: 2 input channels (VV, VH), 1 output logit per pixel."""

    def __init__(self, base: int = 16):
        super().__init__()
        c = [base, base * 2, base * 4, base * 8]
        self.e1, self.e2, self.e3 = block(2, c[0]), block(c[0], c[1]), block(c[1], c[2])
        self.mid = block(c[2], c[3])
        self.pool = nn.MaxPool2d(2)
        self.u3 = nn.ConvTranspose2d(c[3], c[2], 2, stride=2)
        self.d3 = block(c[3], c[2])
        self.u2 = nn.ConvTranspose2d(c[2], c[1], 2, stride=2)
        self.d2 = block(c[2], c[1])
        self.u1 = nn.ConvTranspose2d(c[1], c[0], 2, stride=2)
        self.d1 = block(c[1], c[0])
        self.head = nn.Conv2d(c[0], 1, 1)

    def forward(self, x):
        e1 = self.e1(x)
        e2 = self.e2(self.pool(e1))
        e3 = self.e3(self.pool(e2))
        m = self.mid(self.pool(e3))
        d3 = self.d3(torch.cat([self.u3(m), e3], 1))
        d2 = self.d2(torch.cat([self.u2(d3), e2], 1))
        d1 = self.d1(torch.cat([self.u1(d2), e1], 1))
        return self.head(d1)


def loss_fn(logits, y):
    """BCE + soft Dice on valid pixels only (label -1 = no data is ignored)."""
    valid = (y >= 0).float()
    target = y.clamp(min=0).float()
    bce = nn.functional.binary_cross_entropy_with_logits(logits[:, 0], target, reduction="none")
    bce = (bce * valid).sum() / valid.sum().clamp(min=1)
    p = torch.sigmoid(logits[:, 0]) * valid
    dice = 1 - (2 * (p * target).sum() + 1) / (p.sum() + (target * valid).sum() + 1)
    return bce + dice


def augment(x, y, rng, gain_db: float = 0.0):
    """Rotations/flips; optionally a random brightness shift of up to +-gain_db dB per chip
    (both bands) and +-gain_db/3 dB between bands: radar brightness differs between
    satellites, incidence angles and regions. No-data pixels (0) stay no-data."""
    k = int(rng.integers(4))
    x, y = np.rot90(x, k, axes=(1, 2)), np.rot90(y, k, axes=(0, 1))
    if rng.random() < 0.5:
        x, y = x[:, :, ::-1], y[:, ::-1]
    if gain_db > 0:
        shift = rng.uniform(-gain_db, gain_db) + rng.uniform(-gain_db / 3, gain_db / 3, (2, 1, 1))
        x = np.where(x > 0, np.clip(x + shift / (DB_MAX - DB_MIN), 1e-4, 1.0), 0.0
                     ).astype("float32")
    return np.ascontiguousarray(x), np.ascontiguousarray(y)


class WeightAverage:
    """Exponential moving average of the weights (and BatchNorm statistics): the evaluated
    model is a smooth average of recent steps instead of one noisy step.

    Warm-up (the usual form): the decay after n steps is min(decay, (1 + n) / (10 + n)), so the
    average follows the model closely at first and forgets the random starting weights. Without
    it, 0.995 still keeps ~24 % of the random start after 288 steps (seen 2026-10-13: the
    average scored 0 while the live weights scored 0.60)."""

    def __init__(self, model: nn.Module, decay: float):
        self.decay, self.steps = decay, 0
        self.state = {k: v.detach().clone() for k, v in model.state_dict().items()}

    @torch.no_grad()
    def update(self, model: nn.Module) -> None:
        self.steps += 1
        d = min(self.decay, (1 + self.steps) / (10 + self.steps))
        for k, v in model.state_dict().items():
            if v.dtype.is_floating_point:
                self.state[k].mul_(d).add_(v.detach(), alpha=1 - d)
            else:
                self.state[k].copy_(v)


# --- metrics -----------------------------------------------------------------------------
def scores(pred: np.ndarray, y: np.ndarray) -> dict:
    """Pixel-level IoU / precision / recall / F1 over all valid pixels (aggregated)."""
    valid = y >= 0
    p, t = pred[valid].astype(bool), y[valid] == 1
    tp, fp, fn = int((p & t).sum()), int((p & ~t).sum()), int((~p & t).sum())
    def ratio(num, den):  # None (JSON null), never NaN, when undefined
        return round(num / den, 4) if den else None

    iou, prec, rec = ratio(tp, tp + fp + fn), ratio(tp, tp + fp), ratio(tp, tp + fn)
    f1 = ratio(2 * prec * rec, prec + rec) if prec and rec else None
    return {"iou": iou, "precision": prec, "recall": rec, "f1": f1,
            "valid_pixels": int(valid.sum()), "water_pixels": int(t.sum())}


def threshold_baseline(x: np.ndarray, vv_db_limit: float) -> np.ndarray:
    """The simple rule a non-AI system uses: water where VV backscatter <= limit."""
    vv_db = x[:, 0] * (DB_MAX - DB_MIN) + DB_MIN
    return (vv_db <= vv_db_limit) & (x[:, 0] > 0)


@torch.no_grad()
def predict(model, x: np.ndarray, batch: int = 8) -> np.ndarray:
    model.eval()
    out = [torch.sigmoid(model(torch.from_numpy(x[i:i + batch]))).numpy()[:, 0]
           for i in range(0, len(x), batch)]
    return np.concatenate(out)


def export_onnx(model, path: Path) -> None:
    """Write the model as ONNX (any image size divisible by 8)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    model.eval()
    torch.onnx.export(model, torch.zeros(1, 2, 256, 256), str(path), input_names=["s1_vv_vh"],
                      output_names=["water_logit"], opset_version=17, dynamo=False,
                      dynamic_axes={"s1_vv_vh": {2: "h", 3: "w"},
                                    "water_logit": {2: "h", 3: "w"}})


# --- main --------------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--base", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--ema", type=float, default=0.0,
                    help="weight-average decay per step (0 = off, e.g. 0.995)")
    ap.add_argument("--gain-aug-db", type=float, default=0.0,
                    help="random brightness shift augmentation in dB (0 = off)")
    ap.add_argument("--time-only", action="store_true", help="time one epoch, save nothing")
    ap.add_argument("--out-dir", type=Path, default=MODEL_DIR,
                    help="where to write model, card and checkpoint (candidates go elsewhere "
                         "until they beat the current model on the VALIDATION split)")
    ap.add_argument("--export-only", action="store_true",
                    help="evaluate + export the saved best checkpoint, no training")
    args = ap.parse_args()
    out_dir = args.out_dir
    model_path, card_path = out_dir / MODEL_PATH.name, out_dir / CARD_PATH.name
    checkpoint = out_dir / CHECKPOINT.name
    torch.manual_seed(SEED)
    rng = np.random.default_rng(SEED)
    torch.set_num_threads(max(1, torch.get_num_threads()))

    t0 = time.time()
    data = {name: load_split(name) for name in ("train", "valid", "test", "bolivia")}
    print(f"loaded chips in {time.time() - t0:.0f} s: " +
          ", ".join(f"{k} {len(v[0])}" for k, v in data.items()), flush=True)
    xtr, ytr, _ = data["train"]
    xva, yva, _ = data["valid"]

    model = UNet(args.base)
    n_params = sum(p.numel() for p in model.parameters())
    export_onnx(model, out_dir / "_export_check.onnx")  # fail in seconds, not after an hour
    (out_dir / "_export_check.onnx").unlink()
    t_train = time.time()
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, epochs=args.epochs,
                                                steps_per_epoch=int(np.ceil(len(xtr) /
                                                                            args.batch)))
    best, best_state, history = -1.0, None, []
    averaged = WeightAverage(model, args.ema) if args.ema > 0 else None
    raw = UNet(args.base) if averaged else None  # holds the live weights while evaluating
    if args.export_only:
        saved = torch.load(checkpoint, weights_only=True)
        best_state, history, args.epochs = saved["state"], saved["history"], saved["epochs"]
    for epoch in range(1, (0 if args.export_only else args.epochs) + 1):
        model.train()
        order, t_ep, losses = rng.permutation(len(xtr)), time.time(), []
        for i in range(0, len(order), args.batch):
            pairs = [augment(xtr[j], ytr[j], rng, args.gain_aug_db)
                     for j in order[i:i + args.batch]]
            xb = torch.from_numpy(np.stack([p[0] for p in pairs]))
            yb = torch.from_numpy(np.stack([p[1] for p in pairs]))
            opt.zero_grad()
            loss = loss_fn(model(xb), yb)
            loss.backward()
            opt.step()
            sched.step()
            if averaged:
                averaged.update(model)
            losses.append(float(loss.detach()))
        extra = {}
        if averaged:  # evaluate the averaged weights; report the live ones too
            extra["live_iou"] = scores(predict(model, xva) >= 0.5, yva)["iou"]
            raw.load_state_dict(model.state_dict())
            model.load_state_dict(averaged.state)
        val = scores(predict(model, xva) >= 0.5, yva)
        history.append({"epoch": epoch, "loss": round(float(np.mean(losses)), 4), **val,
                        **extra, "seconds": round(time.time() - t_ep, 1)})
        print(f"epoch {epoch:3d}  loss {np.mean(losses):.4f}  val IoU {val['iou'] or 0:.4f}  "
              + (f"(live weights {extra['live_iou'] or 0:.4f})  " if extra else "")
              + f"({time.time() - t_ep:.0f} s)", flush=True)
        if args.time_only:
            print(f"parameters: {n_params:,}; estimated {args.epochs} epochs: "
                  f"{(time.time() - t_ep) * 40 / 60:.0f} min for 40 epochs")
            return 0
        if (val["iou"] or 0) > best:
            best, best_state = val["iou"], {k: v.clone() for k, v in model.state_dict().items()}
            torch.save({"state": best_state, "history": history, "epochs": args.epochs},
                       checkpoint)  # on disk at once: nothing is lost if export fails
        if averaged:  # continue training from the live weights
            model.load_state_dict(raw.state_dict())

    model.load_state_dict(best_state)
    results = {}
    for name in ("valid", "test", "bolivia"):
        x, y, _ = data[name]
        results[name] = {"model": scores(predict(model, x) >= 0.5, y),
                         "baseline_vv_le_-15dB": scores(threshold_baseline(x, -15.0), y)}
        print(name, json.dumps(results[name]), flush=True)

    export_onnx(model, model_path)
    card = {
        "name": "sampark S1 water U-Net",
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "task": "per-pixel water probability from one Sentinel-1 image (VV, VH in dB)",
        "architecture": f"U-Net, 3 levels, base {args.base} channels, {n_params:,} parameters",
        "trained_from": "scratch (no pretrained weights)",
        "training_data": "Sen1Floods11 hand-labelled S1 chips, official train split (252); "
                         "model selected on the valid split (89)",
        "citation": SEN1FLOODS11_CITATION,
        "licence_note": "trained on Sen1Floods11: non-commercial educational use",
        "input": {"bands": ["VV dB", "VH dB"], "scaling": f"clip((dB - {DB_MIN}) / "
                  f"{DB_MAX - DB_MIN}, 0, 1)", "resolution_m": TRAIN_RESOLUTION_M,
                  "nodata": 0},
        "threshold": 0.5,
        "training": {"epochs": args.epochs, "batch": args.batch, "lr": args.lr,
                     "weight_average_decay": args.ema, "gain_augmentation_db": args.gain_aug_db,
                     "seed": SEED, "device": "CPU",
                     "minutes": round((time.time() - t_train) / 60, 1)},
        "evaluation": results,
        "unseen_event_note": "Bolivia is an entire flood event excluded from training. No "
                             "Nepal/Himalayan chips exist in Sen1Floods11.",
        "history": history,
    }
    card_path.write_text(json.dumps(card, indent=2), encoding="utf-8")
    print(f"saved {model_path} ({model_path.stat().st_size / 1e6:.1f} MB) and model card")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
