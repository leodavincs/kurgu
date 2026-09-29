#!/usr/bin/env python3
"""Screenshots of the timeline on a synthetic project (not a test): python3 tests/ui_tracks_shots.py --out DIR [--prefix tracks] [--views tracks,layers] [--langs en,tr] [--themes dark,light]"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp  # noqa: E402
from tracks_fixture import synthetic  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--prefix", default="tracks")
ap.add_argument("--keys", action="store_true", help="also a shot with keyframe lanes opened under a clip")
ap.add_argument("--views", default="tracks")
ap.add_argument("--langs", default="en,tr")
ap.add_argument("--themes", default="dark,light")
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
srv = cdp.Server(prepare=synthetic)
chrome = cdp.Chrome(width=1600, height=1000)
try:
    for view in a.views.split(","):
        for lang in a.langs.split(","):
            for theme in a.themes.split(","):
                p = chrome.new_page()
                p.open(srv.url("/"), lang=lang, theme=theme, view=view)
                p.eval("document.documentElement.style.setProperty('--lp-h','560px')")
                p.eval("__kurgu.S.selection=['gor4'];__kurgu.S.t=20;__kurgu.emit('time');__kurgu.emit('selection')")
                time.sleep(1.2)
                p.eval("document.querySelector('.lp-body').scrollTop=0")
                time.sleep(0.2)
                r = p.eval("(()=>{const r=document.getElementById('layers').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()")
                clip = {"x": r[0], "y": r[1], "width": r[2], "height": r[3]}
                name = "before" if view == "layers" else "after"
                p.screenshot(os.path.join(a.out, "%s-%s-%s-%s.png" % (a.prefix, name, lang, theme)), clip=clip)
                if a.keys and view == "tracks":
                    p.eval("__kurgu.change(P=>{P.layers.find(x=>x.id==='card1').keys={opacity:[{t:0,v:0},{t:1,v:1}],rotation:[{t:0,v:0},{t:2,v:10}]}})")
                    time.sleep(0.4)
                    p.eval("document.querySelector('.clip[data-id=card1] .clip-kf').click()")
                    time.sleep(0.5)
                    p.screenshot(os.path.join(a.out, "%s-keys-%s-%s.png" % (a.prefix, lang, theme)), clip=clip)
                p.close()
finally:
    chrome.close()
    srv.close()
