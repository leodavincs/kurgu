"""After `kurgu rerender` the open editor shows the NEW clip at the current frame, without a page reload (real rerender: an html-frames page whose colour
changes, headless Chrome both for the render and for the editor). Skipped without Chrome / ffmpeg / websocket-client."""
import os
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp  # noqa: E402
import render_adaptors as RA  # noqa: E402
from test_rerender import PAGE, mkproj  # noqa: E402

try:
    import websocket  # noqa: F401
    HAVE_WS = True
except Exception:  # pragma: no cover
    HAVE_WS = False
pytestmark = pytest.mark.skipif(not (cdp.find_chrome() and HAVE_WS and shutil.which("ffmpeg") and RA.find_chrome()), reason="needs Chrome, ffmpeg, websocket-client")
PIX = "(()=>{const c=document.getElementById('canvas');const d=c.getContext('2d').getImageData(c.width>>1,c.height>>1,1,1).data;return [d[0],d[1],d[2]]})()"


def rerender(d, env):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "kurgu_cli.py"), "rerender", d, "S01"], capture_output=True, text=True, env=env, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr


def big(d):
    """Make the project 1920x1080: such clips are played through a proxy in the editor."""
    import json
    pj = os.path.join(d, "project.json")
    j = json.load(open(pj))
    j["width"], j["height"] = 1920, 1080
    json.dump(j, open(pj, "w"))
    page = os.path.join(d, "gen", "page.html")
    open(page, "w").write(PAGE.replace("320", "1920").replace("180", "1080") % ("#ff0000", ""))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=1920x1080:r=24:d=1", "-pix_fmt", "yuv420p", os.path.join(d, "media", "shot.mp4")], check=True)


@pytest.mark.parametrize("wide", [False, True], ids=["small", "wide-with-proxy"])
def test_the_editor_shows_the_new_clip_after_rerender(tmp_path, wide):
    d = mkproj(tmp_path, audio=False)
    if wide:
        big(d)
    env = dict(os.environ, KURGU_CONFIG=tempfile.mkdtemp(prefix="kurgu-hr-"))
    rerender(d, env)                                                                  # media/shot.mp4 now holds the page's red
    port = cdp.free_port()
    srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), d, "--port", str(port), "--no-open"], cwd=ROOT, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    c = cdp.Chrome(width=1100, height=700)
    try:
        cdp.wait_http(f"http://127.0.0.1:{port}/api/project")
        p = c.new_page()
        p.open(f"http://127.0.0.1:{port}/", lang="en", view=None, mode="edit", ready="window.__kurgu && __kurgu.S.project && document.querySelector('#canvas')")
        p.eval("__kurgu.S.t=0.5; __kurgu.emit('time')")
        p.wait_for("(()=>{const x=%s;return x[0]>150&&x[1]<80&&x[2]<80})()" % PIX, 15)
        with open(os.path.join(d, "gen", "page.html"), "w") as f:
            f.write((PAGE.replace("320", "1920").replace("180", "1080") if wide else PAGE) % ("#00ff00", ""))                                           # the code changes: red -> green
        rerender(d, env)
        t0 = time.time()
        p.wait_for("(()=>{const x=%s;return x[1]>150&&x[0]<80&&x[2]<80})()" % PIX, 20)          # no reload of the page: the editor swaps the file itself
        assert p.eval("__kurgu.S.t") == pytest.approx(0.5, abs=0.05)                  # the playhead did not move
        assert time.time() - t0 < 15
        p.close()
    finally:
        c.close()
        srv.terminate()
        try:
            srv.wait(5)
        except Exception:
            srv.kill()
