"""Download the Sen1Floods11 hand-labelled Sentinel-1 chips (training data, brief: optional).

Only S1Hand (VV/VH backscatter chips), LabelHand (water / not-water labels) and the official
split lists, from the dataset's official bucket. Every file is checked against the bucket's
MD5 checksum; re-running skips files that are already complete. The JRC water folder of the
bucket is a forbidden dataset and is refused by floodmap.data_rules.

Usage:  .venv/Scripts/python scripts/fetch_sen1floods11.py [target_dir]
Cite:   Bonafilia et al., CVPR Workshops 2020 (floodmap.attribution.SEN1FLOODS11_CITATION).
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from floodmap.config import REPO_ROOT
from floodmap.data_rules import ALLOWED_TRAINING_PREFIXES
from floodmap.net import download_training_object, list_training_objects


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "data" / "sen1floods11"
    items = []
    for prefix in ALLOWED_TRAINING_PREFIXES:
        found = list_training_objects(prefix, timeout_s=60, retries=3)
        print(f"{prefix}: {len(found)} files, {sum(int(i['size']) for i in found) / 1e9:.2f} GB")
        items += found
    start, done = time.time(), [0, 0]

    def fetch(item):
        dest = target / item["name"].removeprefix("v1.1/")
        fresh = download_training_object(item, dest, timeout_s=120, retries=3)
        done[0 if fresh else 1] += 1

    with ThreadPoolExecutor(max_workers=4) as pool:  # modest parallelism, public bucket
        list(pool.map(fetch, items))
    where = target.relative_to(REPO_ROOT) if target.is_relative_to(REPO_ROOT) else target
    print(f"downloaded {done[0]}, already present {done[1]}, "
          f"in {time.time() - start:.0f} s, saved to {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
