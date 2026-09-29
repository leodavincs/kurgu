#!/usr/bin/env python3
"""Screenshot tour of the editor (not a test): python3 tests/ui_shots.py --out DIR [--prefix before] [--langs en,tr] [--themes dark,light]

States: editor (video layer selected), editor-text (text layer selected), export, sequence, fontpicker, media-grid, askai, menu, note, dropoverlay.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp  # noqa: E402


def sel(p, ids, t=2.14):
    p.eval("__kurgu.S.selection=%s;__kurgu.S.t=%s;__kurgu.emit('time');__kurgu.emit('selection')" % (str(ids).replace("'", '"'), t))
    time.sleep(0.5)


def inspector_clip(p):
    r = p.eval("(()=>{const r=document.getElementById('inspector').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()")
    return {"x": r[0], "y": r[1], "width": r[2], "height": r[3]}


def tour(p, out, prefix, lang, theme):
    def name(s):
        return os.path.join(out, "%s-%s-%s-%s.png" % (prefix, s, lang, theme))
    sel(p, ["k_a"])
    time.sleep(1.2)
    p.screenshot(name("editor"))
    sel(p, ["k_buyuk"])
    p.screenshot(name("editor-text"))
    box = p.eval("(()=>{const r=document.querySelector('.ai-box').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()")
    p.eval("document.querySelector('.ai-box').scrollIntoView({block:'end'})")
    time.sleep(0.3)
    p.screenshot(name("askai"), clip=inspector_clip(p))
    p.click_sel("#inspector .ai-box textarea.ai-input")
    p.send("Input.insertText", text="Make the title a bit see-through")
    p.click_sel("#inspector .ai-go")
    p.wait_for("document.querySelector('#inspector .ai-status.done')", 30)
    time.sleep(0.4)
    p.eval("document.querySelector('.ai-box').scrollIntoView({block:'end'})")
    time.sleep(0.3)
    p.screenshot(name("askai-done"), clip=inspector_clip(p))
    p.click_sel("#b-undo")
    p.eval("document.getElementById('inspector').scrollTop=0")
    # font picker
    p.click_sel("#inspector .font-btn")
    p.wait_for("document.querySelector('.font-menu .font-item')")
    time.sleep(0.6)
    p.screenshot(name("fontpicker"))
    p.key("Escape")
    time.sleep(0.2)
    # media grid
    p.eval("document.querySelector('.mp-view') && document.querySelector('.mp-view').click()")
    time.sleep(1.0)
    p.screenshot(name("media-grid"))
    p.eval("document.querySelector('.mp-view').click()")
    time.sleep(0.3)
    # add-layer menu
    p.click_sel("#b-add")
    time.sleep(0.4)
    p.screenshot(name("menu"))
    p.key("Escape")
    # note popover
    p.click_sel("#b-note")
    time.sleep(0.3)
    p.screenshot(name("note"))
    p.key("Escape")
    # sequence dialog
    p.click_sel("#b-seq")
    p.wait_for("document.getElementById('seq-modal')")
    time.sleep(0.7)
    p.screenshot(name("sequence"))
    p.key("Escape")
    time.sleep(0.3)
    # export dialog
    p.click_sel("#b-export")
    p.wait_for("document.querySelector('.xp-overlay') && !document.querySelector('.xp-overlay').hidden")
    time.sleep(0.9)
    p.screenshot(name("export"))
    p.eval("document.querySelector('.xp-adv') && (document.querySelector('.xp-adv').open = true)")
    time.sleep(0.4)
    p.screenshot(name("export-advanced"))
    p.eval("document.querySelector('.xp-adv') && (document.querySelector('.xp-adv').open = false)")
    p.key("Escape")
    time.sleep(0.3)
    # drop overlay (forced visible)
    p.eval("document.getElementById('drop-overlay').hidden=false")
    time.sleep(0.4)
    p.screenshot(name("dropoverlay"))
    p.eval("document.getElementById('drop-overlay').hidden=true")
    # toasts (visual only)
    p.eval("document.getElementById('b-snap').click();document.getElementById('b-snap').click()")
    time.sleep(0.3)
    p.screenshot(name("toast"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--prefix", default="shot")
    ap.add_argument("--langs", default="en,tr")
    ap.add_argument("--themes", default="dark")
    a = ap.parse_args()
    chrome = cdp.Chrome()
    srv = cdp.Server(fake_agent=True)
    try:
        for theme in a.themes.split(","):
            for lang in a.langs.split(","):
                p = chrome.new_page()
                try:
                    if theme == "light":
                        p.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "light"}])
                    else:
                        p.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark"}])
                    p.open(srv.url("/"), lang=lang, theme=theme)
                    time.sleep(1.0)
                    tour(p, a.out, a.prefix, lang, theme)
                    print("errors:", [e for e in p.errors if "favicon" not in e])
                finally:
                    p.close()
    finally:
        chrome.close()
        srv.close()


if __name__ == "__main__":
    main()
