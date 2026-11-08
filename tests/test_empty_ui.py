"""Empty project in a real (headless) Chrome: clips already in media/ are offered, one click puts them on the timeline; the card fits the viewer."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp  # noqa: E402

try:
    import websocket  # noqa: F401
    HAVE_WS = True
except Exception:  # pragma: no cover
    HAVE_WS = False

pytestmark = pytest.mark.skipif(not (cdp.find_chrome() and HAVE_WS), reason="Chrome / websocket-client not available")


def test_media_clips_are_offered_on_an_empty_timeline_and_the_card_fits():
    s = cdp.Server(prepare=lambda p: p.update(layers=[]))
    c = cdp.Chrome(width=1100, height=640)
    try:
        p = c.new_page()
        p.open(s.url("/"), lang="en", view=None, mode="review", ready="window.__kurgu && __kurgu.S.project")
        p.wait_for("document.querySelector('.es-found') && !document.querySelector('.es-found').hidden")
        assert "already in media/" in p.eval("document.querySelector('.es-found-head span').textContent")
        assert p.eval("document.querySelector('.es-found-all').textContent") == "add to timeline"
        r = p.eval("(()=>{const a=document.querySelector('.es-card').getBoundingClientRect(),b=document.getElementById('empty-state').getBoundingClientRect();return [a.top>=b.top-1,a.bottom<=b.bottom+1]})()")
        assert r == [True, True]                                                      # the card is not cropped by the viewer
        p.click_sel(".es-found-all")
        p.wait_for("__kurgu.S.project.layers.length>0")
        assert p.eval("document.getElementById('empty-state').hidden")
        p.close()
    finally:
        c.close()
        s.close()
