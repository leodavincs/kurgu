"""Send to agent vs. the editor's 1 s external-change poll (real headless Chrome, own server). The send makes the SERVER write project.json (it stamps
`sent`); the poll must not take that write for an outside change: no "changed outside the editor" toast, no moment where the watch list is empty
("No notes yet"), exactly one undo step. A send that is slow to answer (slow machine) is simulated by holding its response in the page. A real
outside change, during or after the send, is still reported. Also the singular of the notes header ("1 note · all sent").
Skipped without Chrome / websocket-client."""
import json
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import kurgu_paths  # noqa: E402

try:
    import websocket  # noqa: F401
    HAVE_WS = True
except Exception:  # pragma: no cover
    HAVE_WS = False

pytestmark = pytest.mark.skipif(not (cdp.find_chrome() and HAVE_WS), reason="Chrome / websocket-client not available")
WAIT = 60 if cdp.CI else 20

# fetch wrapper for POST /api/notes/send and /api/notes/answer: the request waits for window.__gate (if set) before it goes out (__atGate says it
# got there), its answer waits for window.__hold (if set; __held says the server has answered) and then __holdMs more (a slow send)
SPY = """(()=>{const of=window.fetch;window.__log=[];
  window.fetch=async(u,o)=>{const send=['/api/notes/send','/api/notes/answer'].some(x=>String(u).includes(x));
    if(send&&window.__gate){window.__atGate=true;await window.__gate;}
    const r=await of(u,o);
    if(send&&window.__hold){window.__held=true;await window.__hold;}
    if(send&&window.__holdMs)await new Promise(z=>setTimeout(z,window.__holdMs));
    return r;};
  new MutationObserver(ms=>{for(const m of ms)for(const n of m.addedNodes){if(!n.classList)continue;
    if(n.classList.contains('toast'))__log.push('toast: '+n.textContent);
    if(n.classList.contains('w-empty')||n.classList.contains('np-empty'))__log.push('empty: '+n.className);}})
   .observe(document.body,{subtree:true,childList:true});})()"""


@pytest.fixture(scope="module")
def env():
    s = cdp.Server()
    c = cdp.Chrome(width=1440, height=900)
    p = c.new_page()
    p.open(s.url("/"), lang="en")
    p.eval(SPY)
    yield p, s
    p.close()
    c.close()
    s.close()


def add_note(page, text):
    page.wait_for("(()=>{const e=document.querySelector('#nb-input');if(!e)return false;const r=e.getBoundingClientRect();return r.width>0&&r.height>0})()", WAIT)
    page.click_sel("#nb-input")
    page.send("Input.insertText", text=text)
    page.key("Enter", code="Enter")
    page.wait_for("__kurgu.S.project.notes.some(n=>n.text===%s)" % json.dumps(text))


def write_outside(server, **fields):
    """An outside writer (another tool, the agent) edits project.json on disk."""
    path = os.path.join(server.dir, "project.json")
    p = json.load(open(path, encoding="utf-8"))
    p.update(fields)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(p, f, indent=2)
    kurgu_paths.replace_retry(tmp, path)              # Windows: a plain rename fails while the server is reading the file


def header(page):
    return page.eval("document.querySelector('#review-panel .w-lhead small').textContent")


def test_1_own_send_is_not_an_outside_change(env):
    page, server = env
    add_note(page, "first")
    assert header(page) == "1 note · 1 not sent yet"
    page.eval("window.__holdMs=2600")                       # the answer comes 2.6 s after the server wrote project.json: at least two polls see the new file first
    page.click_sel("#b-watch")
    page.wait_for("document.getElementById('watch') && !document.getElementById('watch').hidden", WAIT)
    page.wait_for("document.querySelectorAll('#watch .w-note').length===1")
    undo0 = page.eval("__kurgu.S.undo.length")
    page.eval("__log.length=0")
    page.click_sel("#wl-send")
    page.wait_for("__kurgu.S.project.notes.find(n=>n.text==='first').sent", WAIT)
    time.sleep(2.5)                                          # a few more polls after the answer
    log = page.eval("__log")
    assert not [x for x in log if "outside" in x or "updated the project" in x], log
    assert not [x for x in log if x.startswith("empty")], log      # the watch list never fell back to "No notes yet"
    assert page.eval("__kurgu.S.undo.length") == undo0 + 1          # the send is one undo step, not two
    assert page.eval("document.querySelectorAll('#watch .w-note.sent').length") == 1
    assert page.eval("document.querySelector('#watch .w-lhead small').textContent") == "1 note · all sent"
    assert header(page) == "1 note · all sent"
    page.key("Escape", code="Escape")
    page.wait_for("document.getElementById('watch').hidden")
    page.eval("window.__holdMs=0")


def test_2_outside_change_after_send_is_still_reported(env):
    page, server = env
    page.eval("__log.length=0")
    write_outside(server, name="Renamed outside")
    page.wait_for("__kurgu.S.project.name==='Renamed outside'", WAIT)
    page.wait_for("__log.some(x=>x.startsWith('toast: The agent updated the project'))")   # sent notes are out: the change is most likely the agent's


def test_3_outside_change_while_send_is_in_flight_is_reported(env):
    page, server = env
    add_note(page, "second")
    page.eval("__log.length=0; window.__atGate=false; window.__gate=new Promise(r=>{window.__open=r})")
    page.click_sel("#b-send")
    page.wait_for("window.__atGate", WAIT)                  # the editor saved and is about to send; the request is held at the gate
    write_outside(server, name="Renamed during send")        # someone else writes now, before the server stamps `sent`
    page.eval("window.__gate=null; window.__open()")
    page.wait_for("__kurgu.S.project.notes.find(n=>n.text==='second').sent", WAIT)
    assert page.eval("__kurgu.S.project.name") == "Renamed during send"
    page.wait_for("__log.some(x=>x.startsWith('toast: The agent updated the project'))")


def test_4_singular_header_in_turkish(env):
    page, server = env
    page.eval("__kurgu.setLang('tr')")
    page.wait_for("document.documentElement.lang==='tr'")
    page.wait_for("document.querySelector('#review-panel .w-lhead small').textContent.startsWith('2 not')")
    assert header(page) == "2 not · hepsi gönderildi"
    page.eval("__kurgu.change(p=>{p.notes=p.notes.filter(n=>n.text!=='second')})")
    page.wait_for("document.querySelector('#review-panel .w-lhead small').textContent==='1 not · hepsi gönderildi'")
    page.eval("__kurgu.setLang('en')")
    page.wait_for("document.querySelector('#review-panel .w-lhead small').textContent==='1 note · all sent'")


BAD = ("changed outside", "agent updated", "edited the project meanwhile")     # the editor's own writes must not look like someone else's


def bad_toasts(page):
    return [x for x in page.eval("__log") if x.startswith("toast:") and any(b in x for b in BAD)]


@pytest.mark.parametrize("when", ["before_the_server_writes", "answer_after_autosave", "answer_before_autosave"])
def test_5_an_edit_made_while_a_send_is_out_is_kept_without_toasts(env, when):
    """The user keeps working while Send runs: before the server stamps (request held at the gate), or after it stamped but before the answer
    is in (answer held), with the 600 ms autosave due before or after that answer. The edit must survive, in the editor and on disk, and the
    editor's own writes must not be reported as an outside change or a merge with the agent."""
    page, server = env
    text, name = "note " + when, "edited " + when
    add_note(page, text)
    page.wait_for("__kurgu.S.saveState==='saved'", WAIT)
    page.eval("__log.length=0; window.__atGate=false; window.__held=false")
    if when == "before_the_server_writes":
        page.eval("window.__gate=new Promise(r=>{window.__open=r})")
    else:
        page.eval("window.__hold=new Promise(r=>{window.__release=r})")
    page.click_sel("#b-send")
    page.wait_for("window.__atGate||window.__held", WAIT)
    page.eval("__kurgu.change(p=>{p.name=%s})" % json.dumps(name))
    if when != "answer_before_autosave":
        time.sleep(1.5)                                      # the autosave would have run by now
    page.eval("window.__gate=null; window.__open&&window.__open(); window.__hold=null; window.__release&&window.__release()")
    page.wait_for("__kurgu.S.project.notes.find(n=>n.text===%s).sent" % json.dumps(text), WAIT)
    page.wait_for("__kurgu.S.saveState==='saved'", WAIT)
    time.sleep(2.2)                                          # a couple of polls
    assert page.eval("__kurgu.S.project.name") == name
    disk = server.project()["project"]
    assert disk["name"] == name and [n for n in disk["notes"] if n["text"] == text][0]["sent"]
    assert not bad_toasts(page), page.eval("__log")


def test_6_answer_saves_a_pending_edit_first_and_keeps_it(env):
    page, server = env
    add_note(page, "which colour?")
    page.click_sel("#b-send")
    page.wait_for("__kurgu.S.project.notes.find(n=>n.text==='which colour?').sent", WAIT)
    nid = page.eval("__kurgu.S.project.notes.find(n=>n.text==='which colour?').id")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "server.py"), "notes", "ask", server.dir, nid, "--question", "red or blue?"],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    page.wait_for("__kurgu.S.project.notes.find(n=>n.id===%s).asked" % json.dumps(nid), WAIT)
    page.wait_for("!!document.querySelector('#review-panel .ask-in')", WAIT)
    time.sleep(1.2)
    page.eval("__log.length=0")
    page.eval("""(()=>{__kurgu.change(p=>{p.name='edited before answering'});               // autosave due in 600 ms ...
      const i=document.querySelector('#review-panel .ask-in');i.value='blue';                      // ... and the answer goes out at once
      i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));})()""")
    page.wait_for("__kurgu.S.project.notes.find(n=>n.id===%s).answer==='blue'" % json.dumps(nid), WAIT)
    page.wait_for("__kurgu.S.saveState==='saved'", WAIT)
    time.sleep(2.2)
    assert page.eval("__kurgu.S.project.name") == "edited before answering"
    assert server.project()["project"]["name"] == "edited before answering"
    assert not bad_toasts(page), page.eval("__log")


def test_7_a_send_that_never_answers_does_not_stop_the_poll_for_good(env):
    page, server = env
    add_note(page, "hangs")
    page.eval("window.__kurguOwnWriteMaxMs=1500; __log.length=0; window.__held=false; window.__hold=new Promise(r=>{window.__release=r})")
    page.click_sel("#b-send")
    page.wait_for("window.__held", WAIT)                    # the server answered; the page never gets it (a hung request)
    write_outside(server, name="written while the send hangs")
    page.wait_for("__kurgu.S.project.name==='written while the send hangs'", WAIT)   # the poll is back after the limit
    page.eval("window.__hold=null; window.__release(); window.__kurguOwnWriteMaxMs=0")
    page.wait_for("__kurgu.S.project.notes.find(n=>n.text==='hangs').sent", WAIT)
    time.sleep(2.2)
    assert page.eval("__kurgu.S.project.name") == "written while the send hangs"       # the late answer does not put the older file back


def test_8_a_send_running_past_the_limit_is_still_the_editors_own_write(env):
    """Past the 60 s limit the waiting autosave runs on the old base while the send's answer is still out: its 409 is against the editor's own
    stamp, so it is merged without the "agent edited… merged" toast, and the poll does not call the stamp the agent's either."""
    page, server = env
    add_note(page, "slow beyond the limit")
    page.wait_for("__kurgu.S.saveState==='saved'", WAIT)
    page.eval("window.__kurguOwnWriteMaxMs=800; __log.length=0; window.__held=false; window.__hold=new Promise(r=>{window.__release=r})")
    page.click_sel("#b-send")
    page.wait_for("window.__held", WAIT)
    page.eval("__kurgu.change(p=>{p.name='mine, past the limit'})")
    time.sleep(3)                                            # the limit passed: the autosave and the poll run while the answer is still out
    page.eval("window.__hold=null; window.__release(); window.__kurguOwnWriteMaxMs=0")
    page.wait_for("__kurgu.S.project.notes.find(n=>n.text==='slow beyond the limit').sent", WAIT)
    page.wait_for("__kurgu.S.saveState==='saved'", WAIT)
    time.sleep(2.2)
    assert page.eval("__kurgu.S.project.name") == "mine, past the limit"
    assert server.project()["project"]["name"] == "mine, past the limit"
    assert not bad_toasts(page), page.eval("__log")


def test_9_save_before_a_render_waits_for_the_running_send(env):
    """A real frame / an AI job saves first and then reads project.json from disk: while a send runs, that save waits for it (it used to give up
    after 1.6 s and the render read the file without the latest edit)."""
    page, server = env
    add_note(page, "render waits")
    page.wait_for("__kurgu.S.saveState==='saved'", WAIT)
    page.eval("__log.length=0; window.__held=false; window.__hold=new Promise(r=>{window.__release=r})")
    page.click_sel("#b-send")
    page.wait_for("window.__held", WAIT)
    try:
        page.eval("""__kurgu.change(p=>{p.name='seen by the render'}); window.__sw=null;
          __kurgu.saveAndWait().then(async ok=>{const d=await (await fetch('/api/project')).json(); window.__sw=[ok,d.project.name];})""")
        time.sleep(2.5)                                      # longer than the old 1.6 s give-up
    finally:
        page.eval("window.__hold=null; window.__release()")
    page.wait_for("window.__sw", WAIT)
    assert page.eval("window.__sw") == [True, "seen by the render"]


def test_10_a_note_deleted_while_it_is_being_sent_is_kept_with_a_plain_reason(env):
    page, server = env
    add_note(page, "deleted while sending")
    page.wait_for("__kurgu.S.saveState==='saved'", WAIT)
    page.eval("__log.length=0; window.__held=false; window.__hold=new Promise(r=>{window.__release=r})")
    page.click_sel("#b-send")
    page.wait_for("window.__held", WAIT)
    page.eval("__kurgu.change(p=>{p.notes=p.notes.filter(n=>n.text!=='deleted while sending')})")
    page.eval("window.__hold=null; window.__release()")
    page.wait_for("__kurgu.S.project.notes.some(n=>n.text==='deleted while sending'&&n.sent)", WAIT)     # the server stamped it: it stays
    page.wait_for("__log.some(x=>x.includes('already sent'))", WAIT)
    assert not bad_toasts(page), page.eval("__log")


@pytest.mark.parametrize("order", ["edit_lands_before_the_stamp", "edit_comes_after_the_stamp"])
def test_11_reloading_while_a_send_runs(env, order):
    """The page is reloaded (or closed) while Send runs, with an edit not saved yet. The leave handler sends that edit at once instead of waiting
    for the send. Before the server stamps: the stamp is written on top of it, both are on disk. After the stamp: the edit is refused (409),
    and the stamp is never overwritten."""
    page, server = env
    page.eval("__kurgu.change(p=>{for(let i=0;i<45;i++)p.notes.push({id:'z_%s_'+i,t:0.2+i*0.25,text:'many %s '+i,author:'user'})})" % (order, order))
    page.wait_for("__kurgu.S.saveState==='saved'", WAIT)
    page.eval("window.__held=false; window.__sendOut=false; " + ("window.__hold=new Promise(r=>{window.__release=r})" if order.endswith("after_the_stamp") else ""))
    page.eval("(()=>{const of=window.fetch;window.fetch=(u,o)=>{if(String(u).includes('/api/notes/send'))window.__sendOut=true;return of(u,o);};})()")
    page.click_sel("#b-send")
    page.wait_for("window.__held" if order.endswith("after_the_stamp") else "window.__sendOut", WAIT)
    name = "edited then reloaded " + order
    page.eval("__kurgu.change(p=>{p.name=%s})" % json.dumps(name))     # the 600 ms autosave has not run yet
    page.reload_and_wait("window.__kurgu && __kurgu.S.project", WAIT)
    page.eval(SPY)                                           # the new document: the spy again, for the tests after this one
    t0 = time.time()
    while time.time() - t0 < WAIT * 3:
        d = server.project()["project"]
        if all(n.get("sent") for n in d["notes"] if n["id"].startswith("z_" + order)):
            break
        time.sleep(0.3)
    d = server.project()["project"]
    assert all(n.get("sent") for n in d["notes"] if n["id"].startswith("z_" + order))     # the stamp is there in both orders
    if order == "edit_lands_before_the_stamp":
        assert d["name"] == name


@pytest.mark.parametrize("ch,n", [("€", 24000), ("a", 80000)])
def test_12_a_large_or_non_ascii_project_still_saves_when_the_tab_is_left(ch, n):
    """Browsers cap keepalive bodies at 64 KiB in BYTES: 24,000 '€' is about 28K characters but over 76K bytes, which a character count let
    through as keepalive and the browser then refused, losing the edit on reload. A big ASCII body goes without keepalive and still arrives."""
    s = cdp.Server()
    c = cdp.Chrome(width=1440, height=900)
    try:
        p = c.new_page()
        p.open(s.url("/"), lang="en", ready="window.__kurgu && __kurgu.S.project")
        p.eval("__kurgu.change(pr=>{pr.layers.find(l=>l.type==='text').text=%s.repeat(%d)})" % (json.dumps(ch), n))
        p.wait_for("__kurgu.S.saveState==='saved'", WAIT)
        p.eval("__kurgu.change(pr=>{pr.name='edited then left'})")               # the 600 ms autosave has not run yet
        p.reload_and_wait("window.__kurgu && __kurgu.S.project", WAIT)
        t0 = time.time()
        while time.time() - t0 < WAIT and s.project()["project"]["name"] != "edited then left":
            time.sleep(0.2)
        assert s.project()["project"]["name"] == "edited then left"
        p.close()
    finally:
        c.close()
        s.close()
