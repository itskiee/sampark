"""3-minute demo video kit (deliverable "3-minute demo video"): one source of truth for the
narration, the on-screen shots and the captions, so they can never drift apart.

Writes into report/video/:
  video_script.md   timed script: what to show, what to say, recording checklist
  captions.srt      captions timed to the script (upload with the video, or burn in)
  title_card.png    1920x1080 opening card
  end_card.png      1920x1080 closing card with every required attribution + disclaimer

Rules (brief + author): maps and screens only, NO images of victims; "educational prototype"
on screen; all attributions on the end card; numbers only as they appear in the case-study
run and validation results (checked by the author while recording).

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
    (0, 10, "Title card (title_card.png).",
     "Sampark. Give it an area and a flood date, and it tells rescue teams where the flood hit, "
     "what was damaged and who is cut off."),
    (10, 24, "Dashboard, Trishuli case study open: the title, the yellow prototype notice and the "
             "four numbers at the top.",
     "Here is the August 2026 debris flood in Nepal's Trishuli valley. It uses only free data "
     "the brief allows: Sentinel-1 radar, Sentinel-2 when clear, the Copernicus elevation model "
     "and OpenStreetMap from before the flood."),
    (24, 42, "Sidebar: New analysis, pick an area (e.g. unseen/wayanad_2024), leave 'Track later "
             "satellite passes' unticked, click Run analysis; show the live progress until it "
             "finishes (speed up, caption 'sped up').",
     "It works for any area and date. I start a new analysis. Each step shows its status, and "
     "if data is missing, like clouds over Sentinel-2, that step says why and the rest still "
     "runs."),
    (42, 60, "Back to the Trishuli run. Tab 'Where did the flood hit?': the flood map, then the "
             "confidence table below it.",
     "Where did the flood hit? Radar images before and after are compared only on the same "
     "orbit track. Terrain masks remove steep slopes and radar shadow, and every area gets a "
     "confidence level. Here: 15.6 square kilometres."),
    (60, 74, "Tab 'What was damaged?': the damage map, the most affected settlements, the bridges.",
     "What was damaged? Buildings, roads and bridges mapped before the flood are overlaid: 3,332 "
     "buildings, 80 kilometres of road and 163 bridges potentially affected."),
    (74, 94, "Tab 'Who is cut off?': the map, then the table; point at one 'Situation' sentence.",
     "Who is cut off? Comparing the road network before and after finds 215 settlements likely "
     "cut off from the nearest town or hospital. Each gets a sentence naming the likely break "
     "and its distance, and a reminder: needs field verification."),
    (94, 106, "Tab 'Rescue priorities' (the ranked table with reasons), then tab 'Landing zones' "
              "(the map with green candidates).",
     "Settlements are ranked for rescue, with the reasons, and nearby flat, dry ground is "
     "suggested as helicopter landing candidates."),
    (106, 116, "Tab 'Later passes': the table, the three change numbers, the possible lakes.",
     "Later satellite passes show the water receding, and flag possible lakes forming behind "
     "debris."),
    (116, 128, "Tab 'Flood path (bonus)': the traced path map and the settlements along it.",
     "And the bonus: from any upstream point, it traces the flood path down the valley on the "
     "elevation model and lists the settlements along it."),
    (128, 138, "Tab 'Situation report': the one-page report; click 'Download PDF'.",
     "It writes a one-page situation report, and a field package for GPS units and phones that "
     "works offline."),
    (138, 155, "Tab 'AI model': the model's water map and the score table.",
     "Our AI component is our own flood model, trained from scratch on Sen1Floods11 and run "
     "locally. It clearly beats a simple threshold rule on held-out images. In Himalayan gorges "
     "it transfers poorly, and we say so."),
    (155, 170, "The Bidur comparison map (validation/results/EMSR927_AOI03_GRA_PRODUCT_v1.png), "
               "then the table in docs/testing/unseen_places.md.",
     "We checked against the official Copernicus EMS maps, used only for checking: where we "
     "flag damage, precision is 91 to 100 percent. And we tested it on {unseen} places it had "
     "never seen."),
    (170, 177, "Tab 'Data & limitations'.",
     "Satellites pass every few days, so it maps damage. It cannot warn minutes ahead."),
    (177, 180, "End card (end_card.png).",
     "Educational prototype: always verify on the ground."),
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
        ("Sampark", 72, NAVY, "bold"),
        ("\"connection\" in Hindi and Nepali", 22, NAVY, "normal"),
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
        ("Sampark · github.com/itskiee/floodmap", 34, NAVY, "bold"),
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
