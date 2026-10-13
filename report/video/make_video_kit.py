"""3-minute demo video kit (deliverable "3-minute demo video"): one source of truth for the
narration, the on-screen shots and the captions, so they can never drift apart.

Writes into report/video/:
  video_script.md   timed script: what to show, what to say, recording checklist
  captions.srt      captions timed to the script (upload with the video, or burn in)
  title_card.png    1920x1080 opening card
  end_card.png      1920x1080 closing card with every required attribution + disclaimer

Rules (brief + owner): maps and screens only, NO images of victims; "educational prototype"
on screen; all attributions on the end card; numbers only as they appear in the case-study
run and validation results (checked by the owner while recording).

Usage: .venv/Scripts/python -I report/video/make_video_kit.py [--unseen N]
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))
from floodmap import attribution  # noqa: E402

NAVY, RED, GREY = "#16324f", "#b2182b", "#555555"

# (start s, end s, what to show, narration)
SEGMENTS = [
    (0, 12, "Title card (title_card.png).",
     "Floodmap. Give it an area and a flood date, and it tells rescue teams where the flood "
     "hit, what was damaged, and who is cut off, from free satellite data only."),
    (12, 30, "Dashboard: open the Trishuli case-study run; overview map.",
     "This is the August 2026 debris flood in Nepal's Bhote Koshi and Trishuli valley. The "
     "system uses only what the brief allows: Sentinel-1 radar, Sentinel-2 when the sky is "
     "clear, the Copernicus elevation model, and OpenStreetMap as it was before the flood."),
    (30, 52, "Sidebar 'New analysis': type a box and a date, click Run; show the live progress "
             "(speed the recording up and show the caption 'sped up').",
     "Judges can choose any area and date. Here I start a new analysis. Every step reports "
     "its status, and if something is missing, say clouds over the optical image, that step "
     "is skipped with a reason and the rest still runs."),
    (52, 75, "Tab 'Where did the flood hit?': flood map with confidence; zoom into the valley.",
     "Where did the flood hit? Radar images before and after are compared only on the same "
     "orbit track. Terrain masks remove slopes where water cannot stand, and every area gets "
     "a confidence level. Here, 15.6 square kilometres are potentially affected."),
    (75, 100, "Tabs 'What was damaged' then 'Who is cut off': cut-off map, then one settlement "
              "sentence.",
     "What was damaged? Over three thousand pre-event buildings, 80 kilometres of road and "
     "163 bridges are potentially affected. Who is cut off? Comparing the road network before "
     "and after finds 215 settlements likely cut off, each with the likely break and its "
     "distance, and a reminder: needs field verification."),
    (100, 120, "Tabs 'Rescue priorities' and 'Landing zones'; then the situation report PDF "
               "and the field-package zip in the download list.",
     "Settlements are ranked for rescue, with helicopter landing-zone candidates nearby. The "
     "system also writes a one-page situation report, and a field package for GPS units and "
     "phones."),
    (120, 142, "Tab 'AI model': the model's water map and its held-out scores.",
     "Our AI component is our own flood segmentation model, trained from scratch on "
     "Sen1Floods11. On held-out test images it clearly beats a simple threshold rule. "
     "In Himalayan gorges it transfers poorly, and we show that honestly: it is a second "
     "opinion, not the main map."),
    (142, 165, "validation/results.md table and the Bidur comparison map "
               "(EMSR927_AOI03_GRA_PRODUCT_v1.png); then docs/testing/unseen_places.md.",
     "We checked our results against the official Copernicus EMS maps, which were never an "
     "input. Where we flag damage, EMS agrees, with ninety-one to one hundred percent "
     "precision. In the narrow gorge we find only about a third of the area, a limitation we "
     "document. We also ran the system on {unseen} places it had never seen."),
    (165, 175, "Tab 'Data & limitations'.",
     "Satellites pass every few days, so this maps the damage. It cannot warn minutes before "
     "a glacier collapse."),
    (175, 180, "End card (end_card.png), hold until the end.",
     "Floodmap is an educational prototype. Always verify on the ground."),
]


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def captions(segments) -> str:
    """Split each narration into caption chunks (<= 2 lines of 42 characters), timed in
    proportion to their length inside the segment."""
    out, i = [], 1
    for start, end, _, text in segments:
        lines = textwrap.wrap(text, 42)
        chunks = [" ".join(lines[k:k + 2]) for k in range(0, len(lines), 2)]
        total = sum(len(c) for c in chunks)
        t = float(start)
        for c in chunks:
            dt = (end - start) * len(c) / total
            two = "\n".join(textwrap.wrap(c, 42))
            out.append(f"{i}\n{srt_time(t)} --> {srt_time(t + dt - 0.05)}\n{two}\n")
            t += dt
            i += 1
    return "\n".join(out)


def card(path: Path, lines: list[tuple[str, float, str, str]]) -> None:
    fig = plt.figure(figsize=(19.2, 10.8), dpi=100)
    fig.patch.set_facecolor("white")
    renderer = fig.canvas.get_renderer()
    y = 0.86
    for text, size, color, weight in lines:
        # measured on the card (bold text is wider), never guessed character counts
        probe = fig.text(0, 0, "", fontsize=size, fontweight=weight)
        wrapped, cur = [], ""
        for word in text.split():
            probe.set_text(f"{cur} {word}".strip())
            if cur and probe.get_window_extent(renderer).width > 0.86 * fig.bbox.width:
                wrapped.append(cur)
                cur = word
            else:
                cur = f"{cur} {word}".strip()
        probe.remove()
        wrapped = wrapped + [cur] if cur else wrapped or [""]
        fig.text(0.5, y, "\n".join(wrapped), ha="center", va="top", fontsize=size,
                 color=color, fontweight=weight, linespacing=1.35)
        y -= (len(wrapped) * size * 1.35 + size * 0.9) / (10.8 * 72)
    fig.savefig(path)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--unseen", default="[N]", help="number of unseen places tested")
    args = ap.parse_args()
    segs = [(a, b, show, say.replace("{unseen}", str(args.unseen)))
            for a, b, show, say in SEGMENTS]

    words = sum(len(s[3].split()) for s in segs)
    md = ["# 3-minute demo video: script", "",
          f"Total {segs[-1][1]} s, {words} spoken words (about {words / segs[-1][1] * 60:.0f} "
          "words per minute: calm pace). Generated by `report/video/make_video_kit.py`; "
          "captions in `captions.srt`.", "",
          "## Recording checklist", "",
          "- Screen 1920×1080, browser zoom 100 %, dashboard in a clean browser window "
          "(no signed-in tabs, no bookmarks bar).",
          "- Never show `.env`, a terminal with tokens, or the Copernicus login page.",
          "- Maps and screens only: **no images of victims** (brief, responsibility note).",
          "- The 'EDUCATIONAL PROTOTYPE' label must be visible (dashboard header).",
          "- Live run: record the real run and speed it up; show the caption 'sped up'.",
          "- Voice: read the narration below; add `captions.srt` as captions (Clipchamp: "
          "Captions → upload, or the platform's subtitle upload).",
          "- Before recording, check every number spoken against the dashboard of the "
          "case-study run.", "",
          "## Script", "",
          "| Time | Show | Say |", "|---|---|---|"]
    for a, b, show, say in segs:
        md.append(f"| {a // 60}:{a % 60:02d}–{b // 60}:{b % 60:02d} | {show} | {say} |")
    (HERE / "video_script.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (HERE / "captions.srt").write_text(captions(segs), encoding="utf-8")

    card(HERE / "title_card.png", [
        ("floodmap", 72, NAVY, "bold"),
        ("Where did the flood hit?  What was damaged?  Who is cut off?", 30, NAVY, "normal"),
        ("From free satellite data: Sentinel-1, Sentinel-2, Copernicus DEM and "
         "pre-event OpenStreetMap", 22, GREY, "normal"),
        ("", 20, GREY, "normal"),
        ("Nandika Agarwal", 26, NAVY, "normal"),
        ("Multimodal AI Hackathon 2026 · Track B: Mapping flood damage from space", 22,
         GREY, "normal"),
        ("EDUCATIONAL PROTOTYPE — NOT AN OPERATIONAL TOOL", 26, RED, "bold"),
    ])
    card(HERE / "end_card.png", [
        ("floodmap · github.com/itskiee/floodmap", 34, NAVY, "bold"),
        (attribution.DISCLAIMER, 18, RED, "bold"),
        ("", 10, GREY, "normal"),
        *[(a, 17, "black", "normal") for a in attribution.DATA_ATTRIBUTIONS],
        ("Training data: " + attribution.SEN1FLOODS11_CITATION, 15, "black", "normal"),
        ("Validation (checking only): " + attribution.EMS_VALIDATION_CREDIT, 15, "black",
         "normal"),
        ("OpenStreetMap-derived data: Open Database License (ODbL).", 15, "black", "normal"),
    ])
    print(f"wrote video_script.md ({words} words), captions.srt, title_card.png, end_card.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
