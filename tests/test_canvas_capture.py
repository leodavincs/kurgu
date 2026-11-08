"""kurgu elements: text, shapes and images drawn with the 2D canvas API are captured with no help from the page.

Synthetic pages in a temp dir (320x180). Chrome tests skip when there is no Chrome; they run one after the other (no xdist).
"""
import os
import re
import struct
import zlib

import pytest

import elements

try:
    elements.find_chrome()
    HAVE_CHROME = True
except elements.ElementsError:
    HAVE_CHROME = False
needs_chrome = pytest.mark.skipif(not HAVE_CHROME, reason="no Chrome / Chromium")

CINECODE_FILM = os.path.expanduser("~/Documents/cinecode/templates/quick/film.html")


def page(body_js, canvas_attrs='width="320" height="180"', head="", extra_html=""):
    return ("<!doctype html><html><body style=\"margin:0\">" + extra_html + f"<canvas id=\"c\" {canvas_attrs}></canvas>"
            "<script>var cv=document.getElementById('c'),g=cv.getContext('2d');" + head +
            "window.READY=Promise.resolve({fps:24,width:320,height:180,frames:100});"
            "window.renderFrame=function(i){g.setTransform(1,0,0,1,0,0);g.globalAlpha=1;g.fillStyle='#101010';g.fillRect(0,0,320,180);" + body_js +
            "};</script></body></html>")


def run(tmp_path, html, frames=(0,), **kw):
    p = tmp_path / "p.html"
    p.write_text(html, encoding="utf-8")
    return elements.measure(str(p), list(frames), size=(320, 180), fps=24, **kw)


def by_label(res, prefix):
    return [e for e in res["elements"] if e["label"].startswith(prefix)]


def box(e, k=0):
    b = e["boxes"][k]
    return b["l"], b["t"], b["r"], b["b"]


@needs_chrome
def test_text_transform_align_baseline(tmp_path):
    res = run(tmp_path, page(
        "g.fillStyle='#fff';g.font='20px sans-serif';"
        "g.textAlign='left';g.textBaseline='alphabetic';g.fillText('LEFT',10,30);"
        "g.save();g.translate(160,100);g.rotate(Math.PI/2);g.textAlign='center';g.textBaseline='middle';g.fillText('ROTATED',0,0);g.restore();"
        "g.save();g.scale(2,2);g.textAlign='right';g.textBaseline='top';g.fillText('BIG',150,60);g.restore();"))
    left, rot, big = by_label(res, "LEFT")[0], by_label(res, "ROTATED")[0], by_label(res, "BIG")[0]
    assert left["kind"] == "text"
    l, t, r, b = box(left)
    assert 8 <= l <= 12 and 10 <= t <= 20 and 28 <= b <= 36 and 40 <= r - l <= 70        # alphabetic baseline at y=30
    l, t, r, b = box(rot)                                                                   # rotated a quarter turn: tall, centred on (160,100)
    assert (b - t) > (r - l) * 2 and abs((l + r) / 2 - 160) < 8 and abs((t + b) / 2 - 100) < 10
    l, t, r, b = box(big)                                                                   # scaled x2, right-aligned at x=300, top at y=120
    assert 290 <= r <= 302 and 115 <= t <= 130 and (r - l) > 40


@needs_chrome
def test_text_moves_across_frames_and_is_one_element(tmp_path):
    res = run(tmp_path, page("g.fillStyle='#fff';g.font='20px sans-serif';g.fillText('Mover',10+i*4,90);"), frames=(0, 5, 10))
    els = by_label(res, "Mover")
    assert len(els) == 1
    assert [b["f"] for b in els[0]["boxes"]] == [0, 5, 10]
    assert els[0]["boxes"][1]["l"] - els[0]["boxes"][0]["l"] == 20


@needs_chrome
def test_two_identical_strings_stay_distinct(tmp_path):
    res = run(tmp_path, page("g.fillStyle='#fff';g.font='20px sans-serif';g.fillText('Twin',10,40);g.fillText('Twin',10,120);"), frames=(0, 3))
    twins = by_label(res, "Twin")
    assert len(twins) == 2 and twins[0]["id"] != twins[1]["id"]
    assert sorted(box(e)[1] for e in twins)[0] < 40 < sorted(box(e)[1] for e in twins)[1]


@needs_chrome
def test_offscreen_canvas_drawn_onto_the_visible_one(tmp_path):
    html = page(
        "og.clearRect(0,0,100,50);og.fillText('Buffered',5,30);g.fillStyle='#fff';g.drawImage(off,100,50);"
        "g.drawImage(off,0,0,100,50,200,120,200,100);"
        "if(window.OffscreenCanvas){var o=new OffscreenCanvas(100,40).getContext('2d');o.font='20px sans-serif';o.fillText('Never shown',5,25);}",
        head="var off=document.createElement('canvas');off.width=100;off.height=50;var og=off.getContext('2d');og.font='20px sans-serif';og.fillStyle='#fff';")
    res = run(tmp_path, html)
    bufs = by_label(res, "Buffered")
    assert len(bufs) == 2                                                     # drawn twice, at two places: two elements
    lefts = sorted(box(e)[0] for e in bufs)
    assert 104 <= lefts[0] <= 110 and 205 <= lefts[1] <= 215                  # offset 1:1, and the 2x scaled copy (x 5*2+200)
    big = [e for e in bufs if box(e)[0] > 150][0]
    small = [e for e in bufs if box(e)[0] < 150][0]
    assert (box(big)[2] - box(big)[0]) > 1.8 * (box(small)[2] - box(small)[0])
    ghost = by_label(res, "Never shown")
    assert ghost and ghost[0]["approx"] is True and ghost[0]["boxes"] == []   # no screen position: recorded, no box


@needs_chrome
def test_detached_and_hidden_canvases(tmp_path):
    html = ("<!doctype html><body style='margin:0'><canvas id=a width=320 height=180></canvas>"
            "<canvas id=h width=320 height=180 style='display:none'></canvas><canvas id=v width=320 height=180 style='visibility:hidden'></canvas>"
            "<canvas id=z width=320 height=180 style='position:absolute;left:0;top:0;width:160px;height:90px'></canvas><script>"
            "window.READY=Promise.resolve({fps:24,width:320,height:180});"
            "window.renderFrame=function(i){['a','h','v','z'].forEach(function(id){var g=document.getElementById(id).getContext('2d');"
            "g.font='20px sans-serif';g.fillStyle='#fff';g.fillText(id=='a'?'Seen':id=='z'?'Scaled':'Hidden '+id,20,100);});};</script>")
    res = run(tmp_path, html)
    assert not by_label(res, "Hidden")
    seen, scaled = by_label(res, "Seen")[0], by_label(res, "Scaled")[0]
    assert 18 <= box(seen)[0] <= 24
    assert 8 <= box(scaled)[0] <= 12 and (box(scaled)[2] - box(scaled)[0]) < 50           # css 160 px for 320 canvas px: half size


@needs_chrome
def test_transparent_text_is_ignored(tmp_path):
    res = run(tmp_path, page("g.font='20px sans-serif';g.fillStyle='rgba(255,255,255,0)';g.fillText('Ghost',10,50);"
                              "g.fillStyle='#fff';g.globalAlpha=0;g.fillText('Faded',10,90);g.globalAlpha=1;g.fillText('Solid',10,130);"))
    assert not by_label(res, "Ghost") and not by_label(res, "Faded") and by_label(res, "Solid")


@needs_chrome
def test_source_lines(tmp_path):
    (tmp_path / "engine.js").write_text("// helper\nvar subtitle = 'Only in the helper';\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("'Only in the helper'", encoding="utf-8")
    html = page("g.fillStyle='#fff';g.font='20px sans-serif';g.fillText('Title card',10,40);g.fillText('Twice here',10,80);"
                "g.fillText('Twice here',10,120);g.fillText('Only in '+'the helper',10,160);g.fillText('Nowhere '+i,150,40);")
    res = run(tmp_path, html)
    title = by_label(res, "Title card")[0]
    assert title["file"] == "p.html" and "line_ambiguous" not in title
    assert open(tmp_path / "p.html").read().splitlines()[title["line"] - 1].count("Title card")
    twice = by_label(res, "Twice here")
    assert all(e.get("line_ambiguous") for e in twice) and all(e["file"] == "p.html" for e in twice)
    helper = by_label(res, "Only in the helper")[0]
    assert (helper["file"], helper["line"]) == ("engine.js", 2) and "line_ambiguous" not in helper
    assert "file" not in by_label(res, "Nowhere")[0]


@needs_chrome
def test_source_root_is_searched(tmp_path):
    root = tmp_path / "src"
    (root / "deep").mkdir(parents=True)
    (root / "deep" / "scene.mjs").write_text('export const t = "From the root";\n', encoding="utf-8")
    sub = tmp_path / "out"
    sub.mkdir()
    p = sub / "p.html"
    p.write_text(page("g.fillStyle='#fff';g.font='20px sans-serif';g.fillText('From '+'the root',10,40);"), encoding="utf-8")
    res = elements.measure(str(p), [0], size=(320, 180), fps=24, source_root=str(root))
    e = by_label(res, "From the root")[0]
    assert e["file"] == "deep/scene.mjs" and e["line"] == 1


def png(path, w=40, h=20):
    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * w for _ in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@needs_chrome
def test_shapes_images_and_background(tmp_path):
    png(tmp_path / "logo.png")
    body = (
        "g.fillStyle='#ff0000';g.save();g.translate(100,90);g.rotate(0.5);g.beginPath();g.arc(0,0,40,0,Math.PI*2);g.fill();g.restore();"      # circle
        "g.fillStyle='#f2c14e';g.fillRect(200,20,80,40);"                                                                                 # rect
        "g.strokeStyle='#22aa22';g.lineWidth=10;g.beginPath();g.moveTo(20,150);g.lineTo(120,150);g.lineTo(120,170);g.stroke();"          # stroked path
        "g.save();g.translate(10,0);var pp=new Path2D();pp.rect(240,100,40,40);g.fillStyle='#3366ff';g.fill(pp);g.restore();"            # Path2D
        "g.drawImage(logo,250,150);"                                                                                                      # image
        "g.globalAlpha=0;g.fillStyle='#abcdef';g.fillRect(150,150,30,20);g.globalAlpha=1;")                                               # alpha 0: skipped
    html = page(body, head="var logo=new Image();logo.src='logo.png';").replace(
        "window.READY=Promise.resolve({fps:24,width:320,height:180,frames:100});",
        "window.READY=logo.decode().then(function(){return {fps:24,width:320,height:180,frames:100};});")
    res = run(tmp_path, html)
    labels = [e["label"] for e in res["elements"]]
    assert not any("#101010" in x for x in labels)                       # the full-frame background fill is skipped
    assert not any("#abcdef" in x for x in labels)
    circle = by_label(res, "circle #ff0000")[0]
    assert circle["kind"] == "shape" and circle["label"] == "circle #ff0000 r≈40" or circle["label"].startswith("circle #ff0000 r≈")
    l, t, r, b = box(circle)
    assert abs((l + r) / 2 - 100) <= 2 and abs((t + b) / 2 - 90) <= 2 and 78 <= r - l <= 120      # rotated square bound of the circle
    rect = by_label(res, "rect #f2c14e")[0]
    assert rect["label"] == "rect #f2c14e 80×40" and box(rect) == (200, 20, 280, 60)
    path = by_label(res, "path #22aa22")[0]
    assert "(stroke)" in path["label"] and box(path) == (15, 145, 125, 175)                       # lineWidth/2 added on every side
    p2 = by_label(res, "rect #3366ff")[0]
    assert box(p2) == (250, 100, 290, 140)                                                         # Path2D bounds under the current transform
    img = by_label(res, "image logo.png")[0]
    assert img["kind"] == "image" and box(img) == (250, 150, 290, 170)


@needs_chrome
def test_shape_keeps_its_id_while_it_moves(tmp_path):
    res = run(tmp_path, page("g.fillStyle='#00ff88';g.beginPath();g.arc(40+i*10,90,30,0,Math.PI*2);g.fill();"), frames=(0, 4, 8))
    circles = by_label(res, "circle #00ff88")
    assert len(circles) == 1 and [b["f"] for b in circles[0]["boxes"]] == [0, 4, 8]
    assert circles[0]["boxes"][2]["l"] - circles[0]["boxes"][0]["l"] == 80


@needs_chrome
def test_particles_merge_into_one_element(tmp_path):
    body = ("g.fillStyle='#ffaa00';for(var k=0;k<60;k++){g.beginPath();g.arc(40+(k%10)*4,50+Math.floor(k/10)*4,3,0,7);g.fill();}"        # a touching cluster, each dot far below the size floor
            "g.fillStyle='#00aaff';g.beginPath();g.arc(250,150,2,0,7);g.fill();")                                                      # a lone dot: dropped as too small
    res = run(tmp_path, page(body))
    cl = [e for e in res["elements"] if e["kind"] == "shape"]
    assert len(cl) == 1 and cl[0]["label"].startswith("shapes #ffaa00") and "x60" in cl[0]["label"]
    l, t, r, b = box(cl[0])
    assert l <= 38 and r >= 76 and t <= 48 and b >= 70


@needs_chrome
def test_shape_count_is_capped(tmp_path):
    body = "g.fillStyle='#8844ff';for(var k=0;k<84;k++){g.fillRect(4+(k%12)*26,4+Math.floor(k/12)*26,20,20+(k%3));}"
    res = run(tmp_path, page(body))
    shapes = [e for e in res["elements"] if e["kind"] == "shape"]
    assert len(shapes) == 60


@needs_chrome
def test_webgl_canvas_is_reported_as_not_captured(tmp_path):
    html = ("<!doctype html><body style='margin:0'><canvas id=c width=320 height=180></canvas><script>"
            "var gl=document.getElementById('c').getContext('webgl')||document.getElementById('c').getContext('experimental-webgl');"
            "window.READY=Promise.resolve({fps:24,width:320,height:180});window.renderFrame=function(i){if(gl){gl.clearColor(i/10,0,0,1);gl.clear(gl.COLOR_BUFFER_BIT);}};</script>")
    res = run(tmp_path, html)
    if not any("WebGL" in w for w in res.get("warnings", [])):
        pytest.skip("no WebGL context in this Chrome")
    assert "kurguElements" in " ".join(res["warnings"]) and "vision" in " ".join(res["warnings"])


@needs_chrome
def test_hook_wins_over_canvas_capture(tmp_path):
    html = page("g.fillStyle='#fff';g.font='20px sans-serif';g.fillText('Both',10,40);").replace(
        "</script>", "window.kurguElements=function(){return [{id:'both',label:'Both',kind:'text',l:9,t:20,r:70,b:45,file:'x.js',line:3}];};</script>")
    res = run(tmp_path, html)
    both = by_label(res, "Both")
    assert len(both) == 1 and both[0]["id"] == "both" and both[0]["file"] == "x.js"


@needs_chrome
def test_no_canvas_switch(tmp_path):
    res = run(tmp_path, page("g.fillStyle='#fff';g.font='20px sans-serif';g.fillText('Off',10,40);"), canvas=False)
    assert res["elements"] == []


@needs_chrome
@pytest.mark.skipif(not os.path.isfile(CINECODE_FILM), reason="cinecode film template not present")
def test_cinecode_quick_film_smoke():
    res = elements.measure(CINECODE_FILM, [0, 20, 40, 60, 80], params="w=540&h=960")
    texts = [e for e in res["elements"] if e["kind"] == "text"]
    assert texts, res.get("warnings")
    w, h = res["size"]
    for e in texts:
        for b in e["boxes"]:
            assert -w <= b["l"] < b["r"] <= 2 * w and -h <= b["t"] < b["b"] <= 2 * h
            assert 3 <= b["b"] - b["t"] <= h / 2


@needs_chrome
def test_letter_by_letter_reveal_is_one_growing_element(tmp_path):
    body = ("g.fillStyle='#fff';g.font='24px sans-serif';var w='Untitled',x=20;"
            "for(var k=0;k<=Math.min(w.length-1,i);k++){g.fillText(w[k],x,100-(k===i?8:0));x+=g.measureText(w[k]).width;}")        # the newest letter is still rising
    res = run(tmp_path, page(body), frames=range(0, 10))
    txt = [e for e in res["elements"] if e["kind"] == "text"]
    assert len(txt) == 1 and txt[0]["label"] == "Untitled"
    assert txt[0]["boxes"][-1]["r"] - txt[0]["boxes"][-1]["l"] > 70


@needs_chrome
def test_words_far_apart_stay_two_elements_and_close_words_join(tmp_path):
    body = ("g.fillStyle='#fff';g.font='24px sans-serif';g.fillText('Left',10,60);g.fillText('Right',200,60);"
            "g.fillText('Hello',10,120);g.fillText('World',75,120);")
    res = run(tmp_path, page(body))
    labels = sorted(e["label"] for e in res["elements"])
    assert labels == ["Hello World", "Left", "Right"]


@needs_chrome
def test_moving_swarm_keeps_one_id_and_a_sparkle_is_dropped(tmp_path):
    body = ("g.fillStyle='#ffaa00';for(var k=0;k<40;k++){g.beginPath();g.arc(40+i*6+(k%8)*5,60+Math.floor(k/8)*5,3,0,7);g.fill();}"
            "if(i===2){g.fillStyle='#00ffff';g.fillRect(250,20,40,40);}"                       # one frame only
            "g.fillStyle='#ff0066';g.fillRect(250,120,40,30);")                                # always there
    res = run(tmp_path, page(body), frames=range(0, 6))
    swarm = [e for e in res["elements"] if e["label"].startswith("shapes #ffaa00")]
    assert len(swarm) == 1 and len(swarm[0]["boxes"]) >= 5
    assert not [e for e in res["elements"] if "#00ffff" in e["label"]]
    assert [e for e in res["elements"] if e["label"].startswith("rect #ff0066")]
