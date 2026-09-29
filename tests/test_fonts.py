import os
import shutil

import pytest
from fontTools.ttLib import TTCollection, TTFont

import fonts

BUNDLED_IDS = ["inter-light", "inter-regular", "inter-medium", "inter-semibold", "inter-bold",
               "cormorant-garamond-regular", "cormorant-garamond-bold", "cormorant-garamond-italic",
               "bebas-neue-regular", "jetbrains-mono-regular", "jetbrains-mono-bold",
               "space-grotesk-regular", "space-grotesk-bold"]
TURKISH = "şğıİöüçŞĞÖÜÇ"


def test_bundled_ids_exist_with_turkish_glyphs():
    ids = {f["id"]: f for f in fonts.bundled_faces()}
    for fid in BUNDLED_IDS:
        assert fid in ids, fid
        assert ids[fid]["source"] == "bundled"
        cm = fonts.cmap(ids[fid])
        assert all(ord(c) in cm for c in TURKISH), f"{fid} lacks Turkish glyphs"


def test_bundled_licences_present():
    lic = os.path.join(fonts.BUNDLED_DIR, "licenses")
    names = os.listdir(lic)
    for fam in ("inter", "cormorantgaramond", "bebasneue", "jetbrainsmono", "spacegrotesk"):
        assert any(n.startswith(fam) and "OFL" in n for n in names), fam


def test_bundled_weights_and_style_metadata():
    ids = {f["id"]: f for f in fonts.bundled_faces()}
    assert ids["inter-bold"]["weight"] == 700 and ids["inter-light"]["weight"] == 300
    assert ids["cormorant-garamond-italic"]["italic"] is True
    assert ids["inter-regular"]["family"] == "Inter"


def test_id_slugs():
    assert fonts.slugify_font_id("Gill Sans", "Light") == "gill-sans-light"
    assert fonts.slugify_font_id("Helvetica Neue", "Regular") == "helvetica-neue-regular"
    assert fonts.slugify_font_id("Noto Sans TC", "Demi-Bold") == "noto-sans-tc-demi-bold"


def make_ttc(path):
    a = TTFont(os.path.join(fonts.BUNDLED_DIR, "Inter-Bold.ttf"))
    b = TTFont(os.path.join(fonts.BUNDLED_DIR, "BebasNeue-Regular.ttf"))
    coll = TTCollection()
    coll.fonts = [a, b]
    coll.save(path)


def test_project_ttc_registry_and_face_extraction(tmp_path):
    (tmp_path / "fonts").mkdir()
    ttc = str(tmp_path / "fonts" / "pair.ttc")
    make_ttc(ttc)
    faces = fonts.project_faces(str(tmp_path))
    assert [(f["id"], f["index"], f["source"]) for f in faces] == [("inter-bold", 0, "project"),
                                                                    ("bebas-neue-regular", 1, "project")]
    face = fonts.get_face("bebas-neue-regular", str(tmp_path))
    extracted = fonts.face_file(face)
    assert extracted.endswith(".ttf") and os.path.join("faces", "bebas-neue-regular") in extracted
    f = TTFont(extracted)
    ref = TTFont(os.path.join(fonts.BUNDLED_DIR, "BebasNeue-Regular.ttf"))
    assert f.getGlyphOrder() == ref.getGlyphOrder()
    assert fonts.face_file(face) == extracted          # second call reuses the cache


def test_priority_project_over_bundled_over_system(tmp_path):
    (tmp_path / "fonts").mkdir()
    shutil.copy(os.path.join(fonts.BUNDLED_DIR, "Inter-Regular.ttf"), tmp_path / "fonts" / "MyInter.ttf")
    fonts.refresh()
    assert fonts.get_face("inter-regular", str(tmp_path))["source"] == "project"
    assert fonts.get_face("inter-regular")["source"] == "bundled"
    reg = {f["id"]: f for f in fonts.registry(str(tmp_path))}
    assert reg["inter-regular"]["source"] == "project"
    assert len([f for f in fonts.registry(str(tmp_path)) if f["id"] == "inter-regular"]) == 1


def test_registry_order_and_unknown_and_fallback():
    assert fonts.get_face("no-such-font-xyz") is None
    font, face, found = fonts.load_pil_font("no-such-font-xyz", 20)
    assert found is False and face["id"] == fonts.FALLBACK_ID
    font, face, found = fonts.load_pil_font("inter-bold", 20)
    assert found and face["id"] == "inter-bold"


def test_system_scan_cache_written(monkeypatch):
    monkeypatch.setenv("KURGU_SYSTEM_FONTS", "1")
    fonts.system_faces()
    assert os.path.exists(os.path.join(fonts.cache_dir(), "fonts.json"))


def test_system_scan_can_be_switched_off():
    assert fonts.system_faces() == []            # conftest sets KURGU_SYSTEM_FONTS=0
    assert {f["source"] for f in fonts.list_faces()} <= {"bundled", "project"}


def test_nonblocking_list_scans_in_background(monkeypatch):
    monkeypatch.setenv("KURGU_SYSTEM_FONTS", "1")
    monkeypatch.setitem(fonts._system_mem, "faces", None)
    monkeypatch.setitem(fonts._system_mem, "stamp", None)
    fonts.list_faces(block=False)                # returns at once with bundled fonts
    t = fonts._bg["thread"]
    assert t is not None
    t.join(120)
    assert not fonts.scanning()
    assert fonts._system_mem["faces"] is not None


def test_broken_font_file_is_ignored(tmp_path):
    (tmp_path / "fonts").mkdir()
    (tmp_path / "fonts" / "bad.ttf").write_bytes(b"not a font")
    assert fonts.project_faces(str(tmp_path)) == []
