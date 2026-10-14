"""The one-page situation report must stay ONE readable page, whatever the content length.

Seen 2026-10-13: after the landing-zone and later-pass lines were added, the Trishuli sit-rep
ran into the attribution footer and past the right edge. The layout now measures text and
shrinks / trims until it fits; this test feeds it far more text than any real run.
"""

from __future__ import annotations

from sampark import sitrep

LONG = ("near Somewhere Very Long Name (1,341 buildings): likely cut off by road (likely "
        "impassable at Some Highway With A Long Name, 2.87 km away, high confidence); likely "
        "lost road access to hospital Some District Hospital With A Long Name")


def test_sitrep_always_fits_one_page(tmp_path, monkeypatch):
    seen = []
    real_page = sitrep._page

    def recording_page(*args, **kwargs):
        fig, y_end, footer_top = real_page(*args, **kwargs)
        seen.append((y_end, footer_top))
        return fig, y_end, footer_top

    monkeypatch.setattr(sitrep, "_page", recording_page)
    monkeypatch.setattr(sitrep, "priorities", lambda r: [LONG] * 12)
    monkeypatch.setattr(sitrep, "breaks", lambda r: [LONG[:120]] * 8)
    monkeypatch.setattr(sitrep, "landing_line", lambda r: [LONG * 2])
    monkeypatch.setattr(sitrep, "multidate_line", lambda r: [LONG * 2])
    results = {"event_name": "test", "event_date": "2026-08-26", "run_id": "r",
               "created_utc": "2026-10-13T00:00:00", "stages": {},
               "aoi": {"area_km2": 100.0, "bbox": [85.0, 27.0, 85.1, 27.1]}}
    paths = sitrep.build(results, tmp_path, tmp_path / "report")
    assert paths["pdf"].is_file() and paths["png"].is_file()
    y_end, footer_top = seen[-1]
    assert y_end >= footer_top, "the text runs into the footer"
    assert len(seen) > 1, "this much text should have needed shrinking or trimming"
    # the plain-text version keeps everything (nothing is lost, only the page is trimmed)
    assert paths["txt"].read_text(encoding="utf-8").count("Some District Hospital") >= 12


def test_sitrep_short_content_uses_full_size(tmp_path, monkeypatch):
    seen = []
    real_page = sitrep._page
    monkeypatch.setattr(sitrep, "_page", lambda *a, **k: seen.append(a[-1]) or real_page(*a, **k))
    results = {"event_name": "test", "event_date": "2026-08-26", "run_id": "r",
               "created_utc": "2026-10-13T00:00:00", "stages": {},
               "aoi": {"area_km2": 100.0, "bbox": [85.0, 27.0, 85.1, 27.1]}}
    sitrep.build(results, tmp_path, tmp_path / "report")
    assert seen == [1.0]  # first attempt fits: no needless shrinking
