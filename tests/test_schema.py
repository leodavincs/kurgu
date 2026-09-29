import json
import os

import pytest

import schema

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "v1_project.json")


def v1():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def by_id(p):
    return {l["id"]: l for l in p["layers"]}


def test_migrate_v1_exact_top_level():
    m = schema.migrate_v1(v1())
    assert {k: m[k] for k in m if k not in ("layers", "notes")} == {
        "version": 2, "name": "Go for Orbit", "width": 1280, "height": 720, "fps": 25, "duration": 20,
        "background": "#101010", "output": "cikti.mp4",
        "global": {"grain": 2, "vignette": 0.3, "loudness_lufs": None, "fade_out": 1.5}}
    assert m["notes"] == [{"id": "n_1", "t": 12.3, "text": "burada ses yüksek", "created": "2026-09-29T15:04:00"}]
    assert [l["id"] for l in m["layers"]] == ["k_yazi", "k_yazi2", "k_gorsel", "k_gorsel2", "k_video", "k_renk", "k_ses"]


def test_migrate_v1_text_layer_exact():
    assert by_id(schema.migrate_v1(v1()))["k_yazi"] == {
        "id": "k_yazi", "name": "Başlık", "type": "text", "group": "Yazı", "hidden": True, "locked": True,
        "start": 1, "end": 5, "text": "GO\nşğıİöüç", "font": "gill-sans-semibold", "size": 100, "color": "#ffffff",
        "letter_spacing": 0.2, "line_height": 1.1, "align": "left", "x": 100, "y": 200, "scale": 1.5, "opacity": 0.8,
        "shadow": {"enabled": False, "blur": 4, "opacity": 0.5},
        "fade_in": 0.5, "fade_out": 0,          # cikis.tur == "yok" -> 0 even though sure was 9
        "effects": [{"type": "grayscale", "enabled": True, "amount": 0.5},
                    {"type": "brightness", "enabled": False, "value": 0.1}]}


def test_migrate_v1_defaults_preserved_where_they_differ():
    m = by_id(schema.migrate_v1(v1()))
    assert m["k_yazi2"]["font"] == "gill-sans-regular"     # v1 default font was "gill"
    assert m["k_gorsel"]["fit"] == "cover"                 # v1 default for images was cover, v2 is contain
    assert m["k_gorsel2"]["fit"] == "native"               # dogal -> native


def test_migrate_v1_media_layers_exact():
    m = by_id(schema.migrate_v1(v1()))
    assert m["k_gorsel"] == {"id": "k_gorsel", "name": "Logo", "type": "image", "src": "medya/logo.png", "start": 0,
                             "end": 3, "crop": {"top": 0.1, "right": 0.2}, "fade_in": 0, "fade_out": 1, "fit": "cover"}
    assert m["k_video"] == {
        "id": "k_video", "name": "Klip", "type": "video", "src": "medya/a.mp4", "src_in": 4, "fit": "contain",
        "start": 0, "end": 10, "volume_db": -6, "muted": True, "envelope": [[0, -20], [2, 0]],
        "fade_in": 0, "fade_out": 0,
        "effects": [{"type": "zoom", "enabled": True, "from": 1, "to": 1.3},
                    {"type": "saturation", "enabled": True, "value": -0.2},
                    {"type": "contrast", "enabled": True, "value": 0.3},
                    {"type": "blur", "enabled": True, "radius": 3},
                    {"type": "freeze", "enabled": True}]}
    assert m["k_renk"] == {"id": "k_renk", "type": "color", "color": "#000000", "start": 9, "end": 10,
                           "fit": "cover", "fade_in": 0, "fade_out": 0}
    assert m["k_ses"]["fade_in"] == 1 and m["k_ses"]["fade_out"] == 2 and m["k_ses"]["type"] == "audio"


def test_v1_font_map_complete():
    assert schema.FONT_MAP_V1 == {
        "gill": "gill-sans-regular", "gill-light": "gill-sans-light", "gill-semibold": "gill-sans-semibold",
        "gill-bold": "gill-sans-bold", "futura": "futura-medium", "futura-bold": "futura-bold",
        "helvetica": "helvetica-neue-regular", "helvetica-light": "helvetica-neue-light",
        "helvetica-bold": "helvetica-neue-bold", "avenir": "avenir-next-regular",
        "avenir-demi": "avenir-next-demi-bold", "avenir-heavy": "avenir-next-heavy"}
    for old, new in schema.FONT_MAP_V1.items():
        p = {"surum": 1, "katmanlar": [{"id": "a", "tur": "yazi", "baslangic": 0, "bitis": 1, "font": old}]}
        assert schema.migrate_v1(p)["layers"][0]["font"] == new


def test_normalize_project_defaults():
    p = schema.normalize({})
    assert p == {"version": 2, "name": "Untitled", "width": 1920, "height": 1080, "fps": 30, "duration": 10,
                 "background": "#000000", "output": "output.mp4",
                 "global": {"grain": 0, "vignette": 0, "loudness_lufs": -14, "fade_out": 0},
                 "layers": [], "notes": []}


def test_normalize_keeps_null_loudness():
    assert schema.normalize({"global": {"loudness_lufs": None}})["global"]["loudness_lufs"] is None


def test_normalize_layer_defaults():
    p = schema.normalize({"width": 1000, "height": 400, "layers": [
        {"id": "t", "type": "text", "start": 0, "end": 2},
        {"id": "v", "type": "video", "src": "a.mp4", "start": 1, "end": 2},
        {"id": "i", "type": "image", "src": "a.png", "start": 1, "end": 2},
        {"id": "a", "type": "audio", "src": "a.wav", "start": 1, "end": 2},
        {"id": "c", "type": "color", "start": 1, "end": 2}]})
    L = by_id(p)
    assert L["t"] == {
        "id": "t", "type": "text", "name": "Text", "group": "", "hidden": False, "locked": False, "start": 0, "end": 2,
        "fade_in": 0, "fade_out": 0, "x": 500, "y": 200, "scale": 1, "opacity": 1, "effects": [],
        "text": "Text", "font": "inter-regular", "size": 48, "color": "#f1efe9", "letter_spacing": 0,
        "line_height": 1.25, "align": "center", "shadow": {"enabled": True, "blur": 9, "opacity": 0.7}}
    assert L["v"]["fit"] == "cover" and L["i"]["fit"] == "contain"
    assert L["v"]["crop"] == {"top": 0, "bottom": 0, "left": 0, "right": 0}
    assert (L["v"]["src_in"], L["v"]["volume_db"], L["v"]["muted"], L["v"]["envelope"]) == (0, 0, False, [])
    assert "x" not in L["a"] and "effects" not in L["a"] and L["a"]["envelope"] == []
    assert L["c"]["color"] == "#000000"


def test_normalize_effects_and_envelope():
    p = schema.normalize({"layers": [{"id": "v", "type": "video", "src": "a", "start": 0, "end": 1,
                                       "envelope": [[2, -3], [0, -9]],
                                       "effects": [{"type": "zoom"}, {"type": "grayscale"}, {"type": "blur"},
                                                   {"type": "contrast", "enabled": False}, {"type": "freeze"}]}]})
    v = p["layers"][0]
    assert v["envelope"] == [[0, -9], [2, -3]]
    assert v["effects"] == [{"type": "zoom", "enabled": True, "from": 1, "to": 1.06},
                            {"type": "grayscale", "enabled": True, "amount": 1},
                            {"type": "blur", "enabled": True, "radius": 0},
                            {"type": "contrast", "enabled": False, "value": 0},
                            {"type": "freeze", "enabled": True}]


def test_normalize_is_idempotent_and_accepts_v1():
    a = schema.normalize(v1())
    assert schema.normalize(a) == a
    assert a["version"] == 2 and a["layers"][0]["type"] == "text"


def test_validate_reports_human_problems():
    probs = schema.validate({"version": 2, "duration": 5, "layers": [
        {"id": "a", "type": "video", "start": 3, "end": 2},
        {"id": "a", "type": "text", "start": 0, "end": 1, "align": "middle", "font": "nope"},
        {"id": "b", "type": "sparkle", "start": 0, "end": 1},
        {"id": "c", "type": "image", "src": "x.png", "start": 0, "end": 1, "opacity": 2,
         "effects": [{"type": "glow"}], "crop": {"top": 0.9}}]}, font_ids=["inter-regular"])
    text = "\n".join(probs)
    for needle in ("end (2) must be greater than start (3)", "missing src", "duplicate id", "align must be one of",
                   'font "nope" not found', "unknown type 'sparkle'", "opacity must be between 0 and 1",
                   "effect #1 has unknown type", "crop.top"):
        assert needle in text, needle


def test_validate_clean_project():
    assert schema.validate(schema.normalize(v1())) == []


def test_load_project_and_migrate_dir(tmp_path):
    (tmp_path / "proje.json").write_text(json.dumps(v1()), encoding="utf-8")
    p = schema.load_project(str(tmp_path))            # in-memory migration
    assert p["version"] == 2 and not (tmp_path / "project.json").exists()
    assert schema.migrate_dir(str(tmp_path)) is True
    assert json.loads((tmp_path / "project.json").read_text(encoding="utf-8")) == p
    assert schema.migrate_dir(str(tmp_path)) is False   # never overwrites
    assert json.loads((tmp_path / "proje.json").read_text(encoding="utf-8")) == v1()


def test_load_project_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        schema.load_project(str(tmp_path))
    (tmp_path / "project.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(schema.SchemaError):
        schema.load_project(str(tmp_path))


# ------------------------------------------------------------------ notes (done / author / layer_ids / reply)
def test_old_notes_are_normalised_with_defaults():
    p = schema.normalize({"notes": [{"id": "n_ab12", "t": 12.3, "text": "loud", "created": "2026-09-29T15:04:00"}, "junk", {"t": "x"}]})
    assert p["notes"][0] == {"id": "n_ab12", "t": 12.3, "text": "loud", "created": "2026-09-29T15:04:00", "done": False,
                             "author": "user", "resolved_at": "", "layer_ids": [], "reply": ""}
    assert p["notes"][1]["id"] == "n_3" and p["notes"][1]["t"] == 0 and p["notes"][1]["text"] == ""


def test_note_fields_are_repaired():
    n = schema.normalize_note({"id": "a", "t": 1, "text": "x", "done": "yes", "author": "robot", "resolved_at": "z",
                               "layer_ids": ["l1", 5, None], "reply": 3})
    assert n["done"] is False and n["author"] == "user" and n["resolved_at"] == "" and n["layer_ids"] == ["l1"] and n["reply"] == ""
    d = schema.normalize_note({"id": "a", "t": 1, "text": "x", "done": True, "author": "agent", "resolved_at": "2026-09-29T16:00:00", "reply": "ok"})
    assert d["done"] and d["author"] == "agent" and d["resolved_at"] == "2026-09-29T16:00:00" and d["reply"] == "ok"


def test_validate_notes():
    base = {"width": 100, "height": 100, "fps": 30, "duration": 5, "layers": []}
    assert schema.validate(dict(base, notes=[{"id": "a", "t": 1, "text": "x"}, {"id": "b", "t": 2, "author": "agent", "done": True}])) == []
    probs = schema.validate(dict(base, notes=[{"id": "a", "t": -1}, {"id": "a", "t": 1, "author": "bot", "done": "no"}]))
    assert len(probs) == 4 and any("duplicate" in x for x in probs) and any("author" in x for x in probs)


def test_group_colour_overrides_are_normalised_and_validated():
    p = {"version": 2, "width": 640, "height": 360, "fps": 30, "duration": 2, "layers": [],
         "groups": {"Cards": {"color": "#C09A55"}, "Bad": {"color": "red"}, "Odd": 3}}
    n = schema.normalize(p)
    assert n["groups"] == {"Cards": {"color": "#c09a55"}}
    assert any('groups["Bad"]' in x for x in schema.validate(p)) and any('groups["Odd"]' in x for x in schema.validate(p))
    assert not schema.validate(n)
    assert "groups" not in schema.normalize({"version": 2, "layers": [], "groups": {"x": {}}})
