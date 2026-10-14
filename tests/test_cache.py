from __future__ import annotations

from datetime import date

from sampark.cache import Cache, stable_hash


def test_stable_hash_ignores_key_order():
    assert stable_hash({"a": 1, "b": [1, 2]}) == stable_hash({"b": [1, 2], "a": 1})
    assert stable_hash({"a": 1}) != stable_hash({"a": 2})


def test_stage_dir_depends_on_settings_and_inputs(tmp_path):
    cache = Cache(tmp_path)
    d = date(2026, 8, 26)
    base = cache.stage_dir("abc", d, "s1", {"threshold_db": 3})
    assert base == cache.stage_dir("abc", d, "s1", {"threshold_db": 3})
    assert base != cache.stage_dir("abc", d, "s1", {"threshold_db": 2})  # new setting
    assert base != cache.stage_dir("xyz", d, "s1", {"threshold_db": 3})  # new AOI
    assert base != cache.stage_dir("abc", date(2026, 8, 27), "s1", {"threshold_db": 3})


def test_half_written_folder_is_not_used_and_gets_cleared(tmp_path):
    cache = Cache(tmp_path)
    folder = cache.stage_dir("abc", date(2026, 8, 26), "s1", {})
    folder.mkdir(parents=True)
    (folder / "partial.tif").write_bytes(b"truncated")  # simulated crash mid-download
    assert not cache.is_complete(folder)

    cache.prepare(folder)
    assert not (folder / "partial.tif").exists()

    (folder / "s1.tif").write_bytes(b"data")
    cache.mark_complete(folder, {"scenes": 4})
    assert cache.is_complete(folder)
    cache.prepare(folder)  # complete folders are never wiped
    assert (folder / "s1.tif").exists()


def test_disabled_cache_never_reports_hits(tmp_path):
    cache = Cache(tmp_path, enabled=False)
    folder = cache.prepare(cache.stage_dir("abc", date(2026, 8, 26), "s1", {}))
    cache.mark_complete(folder)
    assert not cache.is_complete(folder)
