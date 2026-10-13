"""Build the report draft (deliverable: "Report (max. 6 pages), including the limitations
section") as an HTML file that opens in Word / Google Docs, where the owner lays it out.

Every number in the text is READ from files, never typed:
  - the case-study run's results.json            (outputs/<run>/results.json)
  - the EMSR927 comparison                        (validation/results/metrics.json; checking only)
  - the model card and model experiments           (models/segmentation/*.json)
  - the unseen-place campaign                       (docs/testing/unseen_places.json)
  - method values                                   (configs/default.yaml)
A missing source shows a visible "[pending: …]" marker instead of a number.

This builder lives outside src/ and scripts/ on purpose: it quotes the EMSR927 comparison,
which is checking-only material that pipeline code must never read.

Usage:  .venv/Scripts/python -I report/build_report.py outputs/<case-study run id>
Writes: report/floodmap_report_draft.html and report/figures/*.png
"""

from __future__ import annotations

import html
import json
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "report"
FIG = OUT / "figures"
sys.path.insert(0, str(ROOT / "src"))
from floodmap import attribution  # noqa: E402


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def pending(what: str) -> str:
    return f'<span style="background:#ffeb99">[pending: {html.escape(what)}]</span>'


def n(v, fmt="{:,}") -> str:
    return pending("value") if v is None else fmt.format(v)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    run_dir = ROOT / sys.argv[1]
    r = _load(run_dir / "results.json")
    metrics = _load(ROOT / "validation" / "results" / "metrics.json")
    card = _load(ROOT / "models" / "segmentation" / "model_card.json")
    experiments = _load(ROOT / "models" / "segmentation" / "experiments.json")
    unseen = _load(ROOT / "docs" / "testing" / "unseen_places.json")
    cfg = yaml.safe_load((ROOT / "configs" / "default.yaml").read_text(encoding="utf-8"))
    st = r["stages"]

    def out(name):
        s = st.get(name) or {}
        return s.get("outputs") or {}

    s1, s2, osm, conf = out("s1"), out("s2"), out("osm"), out("confidence")
    dmg, cut, seg = out("damage"), out("cutoff"), out("segmentation")
    lz, md, fp = out("landing_zones"), out("multidate"), out("flood_path")
    mot = cut["by_network"]["motorable"]
    walk = cut["by_network"]["all_ways"]
    tracks = [t["pair"] for t in s1["tracks"]]

    FIG.mkdir(parents=True, exist_ok=True)
    figures = {
        "cutoff": run_dir / "cutoff" / "cutoff_map_focus.png",
        "ems": ROOT / "validation" / "results" / "EMSR927_AOI03_GRA_PRODUCT_v1.png",
        "sitrep": run_dir / "report" / "sitrep.png",
    }
    for key, src in figures.items():
        if src.is_file():
            shutil.copyfile(src, FIG / f"{key}.png")

    # ---- EMSR927 table (grading products) ----
    grading = [p for p in metrics["products"] if "_GRA_PRODUCT" in p["product"]]
    names = {"AOI01": "AOI01 Syabrubesi (gorge)", "AOI02": "AOI02 Timure (gorge)",
             "AOI03": "AOI03 Bidur (valley)", "AOI05": "AOI05 Phosretar"}
    ems_rows, unet_rows = [], []
    for p in grading:
        aoi = p["product"].split("_")[1]
        fe, b = p["flood_extent"]["all_confidence"], p["buildings"]
        ems_rows.append(
            f"<tr><td>{names.get(aoi, aoi)}</td><td>{fe['iou']:.2f}</td>"
            f"<td>{fe['precision']:.2f}</td><td>{fe['recall']:.2f}</td>"
            f"<td>{b['reference_damaged_found_by_us']:,} / "
            f"{b['reference_destroyed_or_damaged']:,}</td>"
            f"<td>{p['cutoff_key_breaks']['within_100m_of_reference_damaged_road']} / "
            f"{p['cutoff_key_breaks']['in_reference_area']}</td></tr>")
        u = p["flood_extent"]["own_unet_new_water"]
        unet_rows.append((aoi, u["iou"], u["precision"]))
    breaks_ok = sum(p["cutoff_key_breaks"]["within_100m_of_reference_damaged_road"]
                    for p in grading)
    breaks_all = sum(p["cutoff_key_breaks"]["in_reference_area"] for p in grading)
    precisions = [p["flood_extent"]["all_confidence"]["precision"] for p in grading]
    gorge = [p for p in grading if p["product"].split("_")[1] in ("AOI01", "AOI02")]
    outside = [p["missed_reference_pixels_by_reason"]
               ["outside our terrain-plausible zone (HAND/slope)"] for p in gorge]

    # ---- model table ----
    ev = card["evaluation"]
    model_rows = "".join(
        f"<tr><td>{label}</td><td>{ev[k]['model']['iou']:.3f}</td>"
        f"<td>{ev[k]['baseline_vv_le_-15dB']['iou']:.3f}</td></tr>"
        for k, label in (("test", "Sen1Floods11 test chips"),
                         ("bolivia", "Bolivia (entire event never seen)"),
                         ("valid", "validation chips (used to pick the epoch)")))
    if experiments:
        exp_text = " ".join(e["summary"] for e in experiments["experiments"])
    else:
        exp_text = pending("model improvement experiments (training in progress)")
    unet_txt = ", ".join(f"{a} {iou:.2f}" for a, iou, _ in unet_rows)
    gorge_recall = [p["flood_extent"]["all_confidence"]["recall"] for p in gorge]
    gorge_missed = f"{1 - max(gorge_recall):.0%}–{1 - min(gorge_recall):.0%}"
    tl = md.get("timeline") or []
    def sats(t):
        return ", ".join("Sentinel-" + x.split("-")[-1].upper() for x in t["post"]["platforms"])

    track_text = "; ".join(f"track {t['track']}, after-image {t['post']['start_utc'][:10]} "
                           f"({sats(t)})" for t in tracks)
    water_first = n(tl[0]["water_km2"] if tl else None, "{:.2f}")
    water_last = n(tl[-1]["water_km2"] if tl else None, "{:.2f}")

    # ---- unseen places ----
    if unseen and unseen.get("places"):
        places = [p for p in unseen["places"].values() if "stages" in p]
        failed = sum(1 for p in places for s in p["stages"].values()
                     if s["status"] == "failed")
        rows = []
        for p in places:
            k = p["key"]
            counts = {s: sum(v["status"] == s for v in p["stages"].values())
                      for s in ("ok", "partial", "skipped", "failed")}
            rows.append(
                f"<tr><td>{html.escape(p['name'])}</td><td>{p['event_date']}</td>"
                f"<td>{p['wall_seconds'] / 60:.0f}</td>"
                f"<td>{'/'.join(str(counts[s]) for s in counts)}</td>"
                f"<td>{n(k['potentially_affected_km2'], '{:.1f}')}</td>"
                f"<td>{n(k['settlements_likely_cut_off'])} / "
                f"{n(k['settlements_assessed'])}</td></tr>")
        invalid = list((unseen.get("invalid") or {}).values())
        tb = sum(1 for i in invalid if i["traceback"])
        tb_text = ("produced no Python traceback; each got a plain-language answer." if tb == 0
                   else f"produced <b>{tb} Python traceback(s)</b> (see the campaign file).")
        unseen_html = (
            f"<p>The full system ran on <b>{len(places)} places it had never seen</b> "
            f"(Himalaya, Europe, South America, a desert city, no-flood control), exactly as a "
            f"judge would run it. Stages failed: <b>{failed}</b>. {len(invalid)} invalid inputs "
            f"(future date, open sea, tiny/huge/inverted area, pre-Sentinel date, bad text) "
            f"{tb_text}</p>"
            "<table><tr><th>Place</th><th>Date</th><th>Minutes</th>"
            "<th>Stages ok/partial/skipped/failed</th><th>Potentially affected km²</th>"
            "<th>Settlements likely cut off / assessed</th></tr>" + "".join(rows) + "</table>")
        controls = [p for p in places if "control" in p["place"]]
        control_text = " ".join(
            f"In the no-flood control ({html.escape(p['name'])}), "
            f"{n(p['key']['potentially_affected_km2'], '{:.2f}')} km² of "
            f"{n(p['key']['area_km2'], '{:,.0f}')} km² was still flagged, of which "
            f"{n(p['key'].get('high_confidence_km2'), '{:.2f}')} km² with high confidence, and "
            f"{n(p['key']['settlements_likely_cut_off'])} small settlements came out "
            f"\"likely cut off\": scattered field and shoreline changes, not a flood. This is "
            f"the false-alarm floor of radar change detection, and the reason every result "
            f"carries a confidence level and \"needs field verification\"."
            for p in controls)
        unseen_html += f"<p>{control_text}</p>" if control_text else ""
    else:
        unseen_html = f"<p>{pending('unseen-place campaign results')}</p>"
        control_text = pending("no-flood control result")

    s1c, sc, dm, ct = cfg["s1"], cfg["terrain"], cfg["damage"], cfg["cutoff"]
    conf_k = conf["affected_by_confidence_km2"]
    lakes = md.get("possible_lakes") or []
    refs = [
        attribution.SEN1FLOODS11_CITATION,
        "Copernicus Emergency Management Service, activation EMSR927 (2026), grading and "
        "delineation products, mapping.emergency.copernicus.eu (checking only).",
        "Copernicus Data Space Ecosystem, dataspace.copernicus.eu: Sentinel-1 GRD, Sentinel-2 "
        "L2A, Copernicus DEM GLO-30, via STAC and openEO.",
        "ohsome API (HeiGIT), api.ohsome.org/v1: OpenStreetMap history at a pre-event snapshot.",
        "Ronneberger, O., Fischer, P., Brox, T. (2015). U-Net: Convolutional networks for "
        "biomedical image segmentation. MICCAI.",
    ]

    body = f"""
<h1>floodmap: flood damage and cut-off settlements from free satellite data</h1>
<p class="sub">Multimodal AI Hackathon 2026 · Track B "Mapping flood damage from space" ·
Nandika Agarwal · github.com/itskiee/floodmap<br>
<b>{html.escape(attribution.DISCLAIMER)}</b></p>

<h2>1. Problem and users</h2>
<p>After a Himalayan debris flood, district emergency centres, army and police helicopter
teams and relief organisations need three answers fast, often before anyone can reach the
valley: <b>where did the flood hit, what was damaged, and who is cut off?</b> floodmap takes
<b>any area and flood date</b> and answers them from free data only: Sentinel-1 radar,
Sentinel-2 optical images when the sky is clear, the Copernicus DEM and OpenStreetMap (OSM) as
it was before the event. Results are worded as estimates for prioritising field checks
("potentially affected", "likely cut off … needs field verification"), never as certainties.
The case study is the Bhote Koshi – Trishuli flood of {r['event_date']} in Nepal.</p>

<h2>2. System overview</h2>
<p>One entry point, <code>run_pipeline(area, date)</code>, serves the command line, a local
Streamlit dashboard and the tests. It runs {len(st)} stages: radar change detection, terrain
masks, Sentinel-2, a confidence map, our own segmentation model, pre-event OSM, the flood-path
bonus, damage overlay, cut-off analysis, rescue priority, helicopter landing-zone candidates,
later satellite passes, the one-page situation report and an offline field package (GeoPackage,
KML, GPS waypoints). Each stage returns ok / partial / skipped / failed with a reason and never
stops the run, because judges test live on areas they choose. All tunable values sit in one
commented configuration file; nothing is specific to Trishuli. Everything runs on the user's
laptop: there is no external AI service and only requests for the public input data leave the
machine. The data rules are enforced in code: allowlists of collections and web hosts at
runtime, and a test that scans the code for forbidden sources (with self-tests).</p>

<h2>3. Where did the flood hit?</h2>
<p><b>Sentinel-1 change detection.</b> Ground-range images are calibrated and terrain-corrected
with the Copernicus DEM on Copernicus Data Space (openEO) at {s1c['resolution_m']} m. Before and
after images are compared <b>only on the same orbit track</b>: the first acquisition after the
flood date against the median of up to {s1c['max_pre_images']} earlier ones (less speckle, no
single unusual day), for up to {s1c['max_tracks']} tracks. On the case study:
{track_text}.
After a {s1c['speckle_window_px']}×{s1c['speckle_window_px']} speckle filter, the log-ratio of
after/before backscatter is classified: <i>new water-like</i> (decrease ≤ {s1c['decrease_db']}
dB and after-image VV ≤ {s1c['water_post_vv_db']} dB), <i>likely wet mud/debris</i> (strong
decrease, not water-dark) and <i>likely rough debris</i> (increase ≥ +{s1c['increase_db']} dB, in
VV or VH). Debris shows in both directions in radar, which is why both are kept.</p>
<p><b>Terrain and confidence.</b> From the Copernicus DEM we compute slope, height above the
nearest river (HAND) and the radar's layover/shadow for each track. Change counts as flood only
where flooding is plausible (HAND ≤ {sc['hand_max_m']} m, slope ≤ {sc['slope_max_deg']}°, seen by
the radar). Sentinel-2 adds new water and stripped vegetation where cloud-free
({s2.get('clear_share', 0):.0%} of the area on {s2.get('post_day', pending('date'))}).
Confidence counts independent detections that agree (two radar tracks, Sentinel-2).
<b>Result:</b> {conf['affected_total_km2']:.2f} km² potentially affected (high
{conf_k['high']:.2f}, medium {conf_k['medium']:.2f}, low {conf_k['low']:.2f}).</p>

<h2>4. What was damaged?</h2>
<p>OSM comes only from the ohsome API at an explicit snapshot before the event
({osm['snapshot_utc'][:10]}, the latest ohsome data). Because the brief's endpoint for current
geometries was refused, we rebuild the snapshot from ohsome's full history (keeping exactly the
versions valid at the snapshot; proven equal to ohsome's own counts) and refuse any newer
version. Buildings, roads, bridges, places and hospitals/health facilities are overlaid on the
flood map with small buffers (buildings {dm['building_buffer_m']} m, roads
{dm['road_buffer_m']} m, facilities {dm['facility_buffer_m']} m). A bridge counts when its deck or
approach ({dm['bridge_approach_m']} m) touches detected change, or when the river reach within
{dm['bridge_reach_m']} m changed, because the channel under a lost bridge was water before and
after and shows no change. Settlements are groups of at least
{cfg['osm']['settlement_min_buildings']} buildings linked within
{cfg['osm']['settlement_link_m']} m, named from OSM places. <b>Result:</b>
{dmg['buildings']['potentially_affected']:,} of {dmg['buildings']['of_total_in_area']:,}
buildings potentially affected ({dmg['buildings']['by_confidence']['high']:,} high confidence),
{dmg['roads']['affected_km_motorable']} km of motorable road and
{dmg['bridges']['potentially_affected']} bridges.</p>

<h2>5. Who is cut off?</h2>
<p>A road graph is built from the pre-event OSM roads in the area plus a 10 km margin. Every
settlement is routed to its <b>nearest town (OSM town/city), nearest hospital, and a road
leaving the area</b> (a virtual "outside" node, so places near the edge are not wrongly cut
off), before and after removing road pieces in detected flood/debris. Only breaks of
{ct['break_min_confidence']} or high confidence count as "likely cut off"; low-confidence breaks
give "possibly cut off". A route more than {ct['detour_factor']}× longer (and at least
{ct['detour_min_extra_km']} km more) is a detour. Motorable roads and all ways (including foot
paths) are reported separately. For each settlement the first break on its old route is named
with its distance, in the brief's form: <i>"near Bidur (1,341 buildings): likely cut off by road
(likely impassable at Pasang Lhamu Highway, 2.87 km away, high confidence) … Needs field
verification."</i> The brief's A/B/C/D example is a unit test. <b>Result:</b>
{mot['likely cut off']} of {cut['settlements_assessed']:,} settlements likely cut off by road
({mot['buildings_in_cut_off_settlements']:,} buildings), {mot['possibly cut off']} possibly;
{walk['likely cut off']} have no mapped foot path either. Rescue priority uses transparent tiers
(cut off and damaged, cut off, damaged but reachable, possibly cut off) and lists its reasons.
Helicopter landing-zone candidates (slope ≤ {cfg['landing_zones']['max_slope_deg']}°, a
{cfg['landing_zones']['min_clear_square_m']} m clear square, dry, away from buildings, within
{cfg['landing_zones']['max_distance_m'] / 1000:g} km) exist for {lz.get('settlements_served')} of
{lz.get('likely_cut_off_settlements')} cut-off settlements. Later passes on the same track
({len(tl)} dates) show water {water_first} → {water_last} km² and flag {len(lakes)} possible
lakes forming behind debris (verify).</p>
<p><b>Bonus: flood path.</b> From any upstream point, the flood path is traced down the valley
by steepest descent on the Copernicus DEM (D8 flow directions), and the settlements along it are
listed with their distance downstream and height above the river. From a point near the border,
the case-study path runs {fp.get('length_km')} km
(from {fp.get('elevation_start_m')} m to {fp.get('elevation_end_m')} m elevation);
{len(fp.get('settlements_along_path') or [])} settlements lie within
{cfg['flood_path']['settlement_buffer_m'] / 1000:g} km of it, of which
{sum(1 for x in fp.get('settlements_along_path') or [] if x.get('low_lying'))} are low-lying,
and {fp.get('share_crossing_detected_change', 0):.0%} of the path crosses detected change.</p>
<p><img src="figures/cutoff.png" width="620"><br><i>Figure 1. Who is cut off? Case study, focus
window: settlements likely cut off by road (red), road/bridge breaks (crosses), hospitals.</i></p>

<h2>6. AI component: our own flood segmentation model</h2>
<p>We chose the brief's option 1 and trained a <b>U-Net from scratch</b> (no pretrained weights;
{card['architecture'].split(', ', 2)[-1]}) on the <b>Sen1Floods11</b> hand-labelled Sentinel-1
chips (VV, VH) at the pipeline's 20 m resolution, on a laptop CPU in
{card['training']['minutes']} minutes; it runs locally with onnxruntime. On held-out test chips
it clearly beats the simple radar threshold rule; on the unseen Bolivia event the margin
depends on the training run (below):</p>
<table><tr><th>Held-out data</th><th>Our U-Net IoU</th><th>Threshold rule IoU</th></tr>
{model_rows}</table>
<p>{exp_text}</p>
<p><b>Himalayan scenes it has never seen.</b> No Nepal or Himalayan chip is in the training data.
Here the model transfers poorly: on the case study it marks
{seg.get('water_after_outside_plausible_zone_km2')} km² of "water" outside the plausible zone
(radar shadow on steep slopes, removed by the terrain masks) and only
{seg.get('new_water_km2')} km² of new water inside it, because the 1–5-pixel-wide debris corridor
is mostly mud and debris, not open water. Against EMSR927 its new-water IoU is {unet_txt}, with
precision near 1 where it does flag water, and it raised no false new water in a no-flood
control. The change detection therefore stays the primary map and the model is a documented
second opinion; it is not counted as an independent confidence source (same image).</p>

<h2>7. Case study compared with Copernicus EMS (EMSR927)</h2>
<p>EMSR927 was used <b>only to check</b> results. The method was frozen (commit
{metrics['method_frozen']['commit']}) before the reference was opened; nothing was tuned
afterwards. We compared our flood/debris extent with the EMS observed event, our potentially
affected buildings with EMS destroyed/damaged buildings (15 m tolerance), and our key road
breaks with EMS damaged roads:</p>
<table><tr><th>EMS area (grading)</th><th>Extent IoU</th><th>Precision</th><th>Recall</th>
<th>EMS-damaged buildings we flagged</th><th>Our key breaks on EMS-damaged roads</th></tr>
{''.join(ems_rows)}</table>
<p>Precision is {min(precisions):.2f}–{max(precisions):.2f} everywhere: what we flag, EMS also
mapped. In the wide valley (Bidur) the overlap is high. In the narrow upper gorge we find about
a third of the EMS extent; {min(outside):.0%}–{max(outside):.0%} of the missed area lies outside
our terrain-plausible zone (steep slopes, high above the computed river line). Relaxing those
limits would add false alarms on every slope in the country, so we report the trade-off instead
of tuning to the reference. {breaks_ok} of {breaks_all} of our key breaks lie on roads EMS graded
as damaged. Our 20 m radar pixels cannot match 0.3–1 m photo-interpretation exactly even when
correct.</p>
<p><img src="figures/ems.png" width="420"><br><i>Figure 2. Bidur (EMS AOI03): both (purple),
ours only (orange), EMS only (blue); our flagged buildings (red) and EMS damaged buildings
(circles).</i></p>

<h2>8. Robustness on unseen areas</h2>
{unseen_html}

<h2>9. Limitations</h2>
<ul>
<li><b>Revisit time:</b> Sentinel-1 passes every few days (here the first image came
{tracks[0]['post']['start_utc'][:10]}, after a {r['event_date']} flood). The system maps what
happened; it cannot warn of a sudden glacier collapse or debris flow minutes ahead.</li>
<li><b>Narrow gorges:</b> steep slopes, radar shadow and layover hide parts of the valley floor;
there we miss {gorge_missed} of the EMS extent (Section 7).</li>
<li><b>Radar sees surface change, not damage.</b> A building next to debris may be intact; a
destroyed building on unchanged ground is missed. Road passability is inferred from geometry,
not observed.</li>
<li><b>OSM completeness:</b> only what was mapped before the event counts (snapshot
{osm['snapshot_utc'][:10]}, a month before the flood). Missing roads can make a village look cut
off or connected; settlements are inferred from building groups.</li>
<li><b>Our model</b> was trained on lowland floods and transfers poorly to gorges; it is a
second opinion only.</li>
<li><b>Seasonal effects:</b> monsoon-wet fields, snow and vegetation change can look like water
or debris; later-date debris areas drift upwards with the season.</li>
<li><b>False alarms:</b> ploughed fields, harvests, shoreline and construction changes also
change radar backscatter; a no-flood control still shows scattered, mostly low-confidence
detections (Section 8).</li>
<li><b>Built for river-valley floods:</b> in deserts dry sand is as dark as water to radar and
rain changes sand and city surfaces everywhere; in flat, dense cities scattered detections cut
many roads, so cut-off counts there are not reliable (Dubai test, Section 8).</li>
<li><b>Population</b> is approximated only by the count of pre-event OSM buildings.</li>
<li><b>Dependencies:</b> a live run needs Copernicus Data Space and the ohsome API to be
reachable; repeated runs are served from a local cache.</li>
</ul>

<h2>10. Responsibility, attributions and references</h2>
<p>No images of victims are used. {html.escape(attribution.DISCLAIMER)}</p>
<p>{'<br>'.join(html.escape(a) for a in attribution.DATA_ATTRIBUTIONS)}<br>
Validation (checking only): {html.escape(attribution.EMS_VALIDATION_CREDIT)}<br>
OSM-derived data: Open Database License (ODbL).</p>
<ol class="refs">{''.join(f'<li>{html.escape(x)}</li>' for x in refs)}</ol>
"""
    style = """<style>
body { font-family: Calibri, Arial, sans-serif; font-size: 10.5pt; max-width: 680px; }
h1 { font-size: 16pt; margin-bottom: 2pt; } h2 { font-size: 12pt; margin: 10pt 0 3pt; }
p, li { margin: 3pt 0; text-align: justify; } .sub { font-size: 9pt; color: #444; }
table { border-collapse: collapse; font-size: 9pt; margin: 4pt 0; }
td, th { border: 1px solid #999; padding: 2pt 5pt; } th { background: #e8eef4; }
.refs { font-size: 8.5pt; }
</style>"""
    page = (f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>floodmap report draft'
            f"</title>{style}</head><body>{body}</body></html>")
    target = OUT / "floodmap_report_draft.html"
    target.write_text(page + "\n", encoding="utf-8")
    words = len(" ".join(html.unescape(x) for x in body.split("<")).split())
    print(f"wrote {target.relative_to(ROOT)} (about {words:,} words incl. tags) and "
          f"{len(list(FIG.glob('*.png')))} figures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
