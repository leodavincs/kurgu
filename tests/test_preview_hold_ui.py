"""Play / pause must not flash the picture black (real headless Chrome, own server). Pausing (Space, or typing a note in watch mode) and playing
again re-seek the video elements to the exact playhead; while a <video> seeks it has no drawable frame (readyState drops to HAVE_METADATA),
and the preview used to draw nothing for it, so the background (black) showed for a frame or more each time. The canvas is sampled on every
animation frame around each play / pause; its brightness must never collapse. Skipped without Chrome / websocket-client / H.264."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp  # noqa: E402

try:
    import websocket  # noqa: F401
    HAVE_WS = True
except Exception:  # pragma: no cover
    HAVE_WS = False

pytestmark = pytest.mark.skipif(not (cdp.find_chrome() and HAVE_WS), reason="Chrome / websocket-client not available")
WAIT = 60 if cdp.CI else 20

# mean brightness (0-255) of the preview canvas, once per animation frame, with the editor's state and the video elements' readyState
SAMPLER = """(()=>{window.__lum=[];const cv=document.getElementById('canvas');
  const tmp=document.createElement('canvas');tmp.width=32;tmp.height=18;const tx=tmp.getContext('2d',{willReadFrequently:true});
  const tick=()=>{tx.drawImage(cv,0,0,32,18);const d=tx.getImageData(0,0,32,18).data;let s=0;for(let i=0;i<d.length;i+=4)s+=d[i]+d[i+1]+d[i+2];
    __lum.push([Math.round(s/(32*18*3)),__kurgu.S.playing?1:0,[...document.querySelectorAll('video')].map(v=>v.readyState+(v.seeking?'s':'')).join(' ')]);
    requestAnimationFrame(tick)};requestAnimationFrame(tick);})()"""


@pytest.fixture(scope="module")
def page():
    s = cdp.Server()
    c = cdp.Chrome(width=1280, height=720, scale=2)
    p = c.new_page()
    p.open(s.url("/"), lang="en", mode="review")
    yield p
    p.close()
    c.close()
    s.close()


def test_play_pause_in_watch_mode_keeps_the_picture(page):
    cdp.require_h264(page)
    page.eval("__kurgu.S.t=2.5; __kurgu.emit('time')")
    page.wait_for("[...document.querySelectorAll('video')].filter(v=>v.readyState>=2&&!v.seeking).length>=2", WAIT)
    page.click_sel("#b-watch")
    page.wait_for("document.getElementById('watch') && !document.getElementById('watch').hidden", WAIT)
    time.sleep(0.8)
    page.eval(SAMPLER)
    time.sleep(0.3)
    steps = [("Space", " ", None), ("Space", " ", None),            # play, pause
             ("Space", " ", None), ("KeyA", "a", "a"),              # play, typing a note pauses
             ("Enter", "Enter", None)]                              # Enter adds it and plays on
    for code, key, text in steps:
        page.key(key, code=code, text=text)
        time.sleep(0.6)
    page.key(" ", code="Space")                                     # pause at the end
    time.sleep(0.6)
    lum = page.eval("__lum")
    assert sum(1 for i in range(1, len(lum)) if lum[i][1] != lum[i - 1][1]) >= 4, lum   # it really played and paused
    base = sorted(x[0] for x in lum)[len(lum) // 2]
    assert base > 40, lum                                           # the clips are on screen
    dark = [x for x in lum if x[0] < base * 0.5]
    assert not dark, (base, dark)


VIDEOS_READY = "[...document.querySelectorAll('video')].filter(v=>v.readyState>=2&&!v.seeking).length>=2 && [...document.querySelectorAll('video')].every(v=>!v.seeking)"


def test_held_picture_is_no_bigger_than_the_preview(page):
    """A held picture only stands in for a frame or two: it is kept at the preview canvas's size at most (long side), not the clip's."""
    cdp.require_h264(page)
    made = page.eval("__kurgu.debugInfo().holds.made")
    page.eval("__kurgu.S.t=2.6; __kurgu.emit('time')")
    page.wait_for(VIDEOS_READY, WAIT)
    time.sleep(0.3)                                                 # drawn at least once
    page.eval("__kurgu.S.t=3.4; __kurgu.emit('time')")             # a jump: the clips on screen are held while they seek
    page.wait_for(VIDEOS_READY, WAIT)
    st = page.eval("__kurgu.debugInfo().holds")
    assert st["made"] > made, st
    canvas = page.eval("(()=>{const c=document.getElementById('canvas');return Math.max(c.width,c.height)})()")
    assert max(st["maxW"], st["maxH"]) <= canvas, (st, canvas)


def test_a_stalled_seek_lets_the_held_picture_go(page):
    """A decode that never delivers the frame must not freeze an old picture on screen: after about 2 s the hold is dropped."""
    cdp.require_h264(page)
    page.eval("__kurgu.S.t=2.8; __kurgu.emit('time')")
    page.wait_for(VIDEOS_READY, WAIT)                               # the previous seek is over and the clips are drawn
    time.sleep(0.3)
    page.eval("""(()=>{__kurgu.S.t=3.2; __kurgu.emit('time');
      for(const v of document.querySelectorAll('video'))Object.defineProperty(v,'readyState',{get:()=>1,configurable:true});})()""")
    time.sleep(0.3)
    assert page.eval("__kurgu.debugInfo().holds.live") > 0           # standing in for the stalled clips
    page.wait_for("__kurgu.debugInfo().holds.live===0", 5)
    page.eval("for(const v of document.querySelectorAll('video'))delete v.readyState; __kurgu.S.t=3.3; __kurgu.emit('time')")
