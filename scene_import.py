"""kurgu import <folder>: build a Kurgu project from the scene / shot list of a video that is made with code.

Recognised folders:
  cinecode      shots.json (+ the newest rendered mp4 in renders/, + audio/mix.json when it exists)
  scenes.json   {"fps": 30, "video": "out/film.mp4", "scenes": [{"id": "intro", "start_frame": 0, "end_frame": 90, "file": "scenes/intro.html"}]}
  Remotion      recognised, but not supported yet: the message says which scenes.json to write

The result is one video layer per scene, cut at the scene's frame bounds, each with a `source` field that says which scene
of which folder it came from (so a note on that clip can be fixed in the code, not on top of the video).
cinecode is an MIT project by sarpstar; only its public file formats (shots.json, audio/mix.json, renders/) are read here.
"""
import json
import os
import kurgu_paths
import re
import shutil
import sys

import schema

BUS_DB = {"dialogue": 0, "foley": -1, "sfx": -2, "ambience": -3, "music": -4}      # cinecode's mix.py bus offsets
DEFAULT_STEP = 6


class SceneImportError(Exception):
    pass


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise SceneImportError(f"cannot read {os.path.basename(path)}: {e}")


def _rel(path, start):
    try:
        return os.path.relpath(path, start).replace(os.sep, "/")
    except ValueError:                 # another drive on Windows
        return path.replace(os.sep, "/")


# ---------------------------------------------------------------- recognising a folder
def looks_like_remotion(folder):
    if any(os.path.isfile(os.path.join(folder, n)) for n in ("remotion.config.ts", "remotion.config.js", "remotion.config.mjs")):
        return True
    pkg = os.path.join(folder, "package.json")
    if os.path.isfile(pkg):
        try:
            j = _read_json(pkg)
        except SceneImportError:
            return False
        deps = {**(j.get("dependencies") or {}), **(j.get("devDependencies") or {})}
        return any(k == "remotion" or k.startswith("@remotion/") for k in deps)
    return False


REMOTION_MESSAGE = (
    "This looks like a Remotion project. Importing Remotion compositions is not supported yet.\n"
    "Render your composition to an mp4 (npx remotion render <id> out/film.mp4) and write a scenes.json in the project folder:\n"
    '  {"fps": 30, "video": "out/film.mp4", "scenes": [{"id": "intro", "start_frame": 0, "end_frame": 90}, ...]}\n'
    "(frame numbers are the composition's; a Sequence's `from` and `durationInFrames` are exactly start_frame and end_frame - start_frame)\n"
    "Then run: kurgu import <folder>")


def detect(folder):
    """-> ("scenes" | "cinecode" | "remotion", path-of-list) or raises SceneImportError with a helpful message."""
    sj = os.path.join(folder, "scenes.json")
    if os.path.isfile(sj):
        return "scenes", sj
    cj = os.path.join(folder, "shots.json")
    if os.path.isfile(cj):
        return "cinecode", cj
    if looks_like_remotion(folder):
        raise SceneImportError(REMOTION_MESSAGE)
    raise SceneImportError(f"nothing to import in {os.path.basename(folder) or folder}: expected shots.json (cinecode) or scenes.json "
                       '({"fps": 30, "video": "film.mp4", "scenes": [{"id": "s1", "start_frame": 0, "end_frame": 90}]})')


def find_cinecode_render(folder):
    """The newest rendered film: renders/film-vN.mp4 (final) or renders/draft-vN.mp4, by modification time; else the delivered
    <Title>.mp4 in the folder. Returns a path or None."""
    cands = []
    rdir = os.path.join(folder, "renders")
    if os.path.isdir(rdir):
        for n in os.listdir(rdir):
            if re.fullmatch(r"(film|draft)-v\d+\.mp4", n):
                cands.append(os.path.join(rdir, n))
    if not cands:
        for n in os.listdir(folder):
            if n.lower().endswith(".mp4") and not n.lower().endswith(("-draft.mp4", "-x.mp4")):
                cands.append(os.path.join(folder, n))
    if not cands:
        for n in os.listdir(folder):
            if n.lower().endswith(".mp4"):
                cands.append(os.path.join(folder, n))
    return max(cands, key=os.path.getmtime) if cands else None


# ---------------------------------------------------------------- reading the lists into one shape
def _text_items(v):
    out = []
    for t in (v or []):
        s = t.get("text") if isinstance(t, dict) else t
        if isinstance(s, str) and s.strip():
            out.append(s.strip())
    return out


def read_cinecode(folder, path):
    film = _read_json(path)
    shots = film.get("shots")
    if not isinstance(shots, list) or not shots:
        raise SceneImportError("shots.json has no shots list")
    fps = float(film.get("fps") or 24)
    scene_file = None
    for cand in ("film.html", os.path.join("engine", "index.html")):
        if os.path.isfile(os.path.join(folder, cand)):
            scene_file = cand.replace(os.sep, "/")
            break
    units = []
    for i, s in enumerate(shots):
        if not isinstance(s, dict) or not isinstance(s.get("start"), (int, float)) or not isinstance(s.get("end"), (int, float)):
            raise SceneImportError(f"shot #{i + 1} needs integer start and end frames")
        units.append({"id": str(s.get("id") or f"S{i + 1:02d}"), "start": int(s["start"]), "end": int(s["end"]),
                      "file": scene_file, "text": _text_items(s.get("text_on_screen")), "vo": (s.get("vo") or "").strip() if isinstance(s.get("vo"), str) else "",
                      "description": s.get("description") if isinstance(s.get("description"), str) else ""})
    video = find_cinecode_render(folder)
    return {"kind": "cinecode", "fps": fps, "width": int(film.get("width") or 0), "height": int(film.get("height") or 0),
            "title": film.get("title") if isinstance(film.get("title"), str) else "", "units": units, "video": video,
            "render": "html-frames", "page_params": "capture=1", "film_text": _text_items(film.get("text_on_screen"))}


def _confine(folder, rel, what):
    """`rel` (a path written in scenes.json) resolved inside the imported folder: refuses absolute paths, `..` escapes and symlinks that leave it.
    A list from a downloaded folder must not make Kurgu (or the page measurer, or `kurgu rerender`) touch files elsewhere on the machine."""
    if not isinstance(rel, str) or not rel or os.path.isabs(rel) or rel.startswith(("/", "\\", "~")) or re.match(r"^[A-Za-z]:", rel) or "\x00" in rel:
        raise SceneImportError(f"scenes.json {what} must be a relative path inside the folder, not {rel!r}")
    root = os.path.realpath(folder)
    p = os.path.realpath(os.path.join(root, rel))
    if p != root and not p.startswith(root + os.sep):
        raise SceneImportError(f"scenes.json {what} {rel!r} leaves the folder")
    return os.path.normpath(os.path.join(folder, rel))


def read_scenes(folder, path):
    j = _read_json(path)
    scenes = j.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise SceneImportError("scenes.json has no scenes list")
    fps = j.get("fps")
    if not isinstance(fps, (int, float)) or fps <= 0:
        raise SceneImportError('scenes.json needs "fps" (a number)')
    video = j.get("video")
    video = _confine(folder, video, "video") if isinstance(video, str) and video else None
    units = []
    for i, s in enumerate(scenes):
        if (not isinstance(s, dict) or not isinstance(s.get("start_frame"), (int, float)) or not isinstance(s.get("end_frame"), (int, float))):
            raise SceneImportError(f"scene #{i + 1} needs integer start_frame and end_frame")
        f = s.get("file") if isinstance(s.get("file"), str) and s.get("file") else None
        if f:
            _confine(folder, f, "file")
        units.append({"id": str(s.get("id") or f"scene-{i + 1}"), "start": int(s["start_frame"]), "end": int(s["end_frame"]), "file": f,
                      "text": [], "vo": "", "description": s.get("description") if isinstance(s.get("description"), str) else ""})
    kind = j.get("kind") if isinstance(j.get("kind"), str) and j.get("kind") else "html"
    any_html = any(u["file"] and u["file"].lower().endswith((".html", ".htm")) for u in units)
    return {"kind": kind, "fps": float(fps), "width": int(j.get("width") or 0), "height": int(j.get("height") or 0), "title": str(j.get("title") or ""),
            "units": units, "video": video, "render": "html-frames" if any_html else None, "page_params": str(j.get("page_params") or ""), "film_text": []}


# ---------------------------------------------------------------- building the project
def _num(x):
    x = round(float(x), 4)
    return int(x) if x == int(x) else x


def _probe(path):
    """-> (duration seconds, width, height, has_audio) of a media file, or None when ffprobe cannot read it."""
    import media_tools
    try:
        j = media_tools.probe_json(path, "-show_streams")
    except Exception:
        return None
    dur = float((j.get("format") or {}).get("duration") or 0)
    w = h = 0
    audio = False
    for s in j.get("streams") or []:
        if s.get("codec_type") == "video" and not w:
            w, h = int(s.get("width") or 0), int(s.get("height") or 0)
        elif s.get("codec_type") == "audio":
            audio = True
    return dur, w, h, audio


def _place(src, outdir, copy, warns=None, sub=""):
    """Where a layer's `src` points: the file itself (relative path from the project folder), or a copy under media/ with --copy
    (or when no relative path exists, e.g. another drive)."""
    if not copy:
        rel = _rel(src, outdir)
        if not os.path.isabs(rel) and not re.match(r"^[A-Za-z]:", rel):
            return rel
        if warns is not None:
            warns.append(f"{os.path.basename(src)} is on another drive: copied into the project")
    return _rel(_copy_in(src, os.path.join(outdir, "media", sub)), outdir)


def _copy_in(src, dest_dir, name=None):
    os.makedirs(dest_dir, exist_ok=True)
    dst = os.path.join(dest_dir, name or os.path.basename(src))
    if not (os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src) and int(os.path.getmtime(dst)) >= int(os.path.getmtime(src))):
        shutil.copy2(src, dst)
    return dst


def audio_layers(folder, outdir, copy=False):
    """Layers for audio/mix.json when it exists and its files are on disk: [(layer, warning)...]."""
    mj = os.path.join(folder, "audio", "mix.json")
    if not os.path.isfile(mj):
        return [], []
    plan = _read_json(mj)
    layers, warns = [], []
    for i, l in enumerate(plan.get("layers") or []):
        if not isinstance(l, dict) or not isinstance(l.get("file"), str):
            continue
        src = os.path.normpath(os.path.join(folder, l["file"]))
        base = os.path.realpath(folder)
        if not os.path.realpath(src).startswith(base + os.sep) or not os.path.isfile(src):
            warns.append(f"audio layer {l.get('file')!r} is missing or outside the folder: skipped")
            continue
        info = _probe(src)
        if not info:
            warns.append(f"audio layer {l.get('file')!r} could not be read: skipped")
            continue
        trim = l.get("trim") if isinstance(l.get("trim"), list) and len(l["trim"]) == 2 else None
        src_in = float(trim[0]) if trim else 0.0
        length = float(trim[1]) - src_in if trim else (float(l["length"]) if l.get("loop") and l.get("length") else info[0])
        at = float(l.get("at") or 0)
        rel = os.path.relpath(src, folder)
        dst_src = _place(src, outdir, copy, warns, os.path.dirname(rel))
        bus = l.get("bus") if l.get("bus") in BUS_DB else None
        db = float(l.get("gain_db") or 0) + (BUS_DB[bus] if bus else 0)
        layers.append({"type": "audio", "id": f"audio-{i + 1}", "name": f"{os.path.splitext(os.path.basename(src))[0]}" + (f" ({bus})" if bus else ""),
                       "group": "Sound", "start": _num(at), "end": _num(at + max(0.1, length)), "src": dst_src, "src_in": _num(src_in),
                       "volume_db": _num(db), "fade_in": _num(l.get("fade_in") or 0), "fade_out": _num(l.get("fade_out") or 0)})
    return layers, warns


def build(folder, out=None, video=None, elements=False, step=DEFAULT_STEP, force=False, copy=False, log=print):
    """Import `folder` into a Kurgu project at `out` (default <folder>/kurgu). Returns the written project dict."""
    folder = os.path.realpath(os.path.expanduser(folder))
    if not os.path.isdir(folder):
        raise SceneImportError(f"not a folder: {folder}")
    kind, path = detect(folder)
    spec = read_cinecode(folder, path) if kind == "cinecode" else read_scenes(folder, path)
    out = os.path.realpath(os.path.expanduser(out)) if out else os.path.join(folder, "kurgu")
    pj = schema.project_path(out)
    if os.path.exists(pj) and not force:
        raise SceneImportError(f"{pj} already exists: not overwriting it (use --force to replace it)")
    if video:
        spec["video"] = os.path.realpath(os.path.expanduser(video))
    vpath = spec["video"]
    if not vpath or not os.path.isfile(vpath):
        where = "renders/film-vN.mp4 or renders/draft-vN.mp4" if kind == "cinecode" else 'the "video" file of scenes.json'
        raise SceneImportError(f"no rendered video found ({where}); render the film first, or pass --video <file.mp4>")
    info = _probe(vpath)
    if not info:
        raise SceneImportError(f"cannot read the video {os.path.basename(vpath)} (is ffmpeg installed? try: kurgu doctor)")
    vdur, vw, vh, vaudio = info
    fps = spec["fps"]
    W, H = spec["width"] or vw or 1920, spec["height"] or vh or 1080
    warns = []
    if vw and vh and abs(vw / vh - W / H) > 0.02:
        warns.append(f"the video is {vw}x{vh} but the list says {W}x{H}: the project uses {vw}x{vh}")
        W, H = vw, vh
    os.makedirs(out, exist_ok=True)
    vsrc = _place(vpath, out, copy, warns)
    alayers, awarns = audio_layers(folder, out, copy) if kind == "cinecode" else ([], [])
    warns += awarns
    use_mix = bool(alayers)
    root_rel = _rel(folder, out)
    units = sorted(spec["units"], key=lambda u: (u["start"], u["id"]))
    layers = []
    last_end = 0.0
    for u in units:
        start, end = u["start"] / fps, u["end"] / fps
        if end <= start:
            warns.append(f"{u['id']}: end frame {u['end']} is not after start frame {u['start']}: skipped")
            continue
        if vdur and start >= vdur - 0.01:
            warns.append(f"{u['id']}: starts after the end of the video ({vdur:.2f}s): skipped")
            continue
        if vdur and end > vdur + 0.01:
            warns.append(f"{u['id']}: ends at {end:.2f}s but the video is {vdur:.2f}s: cut short")
            end = vdur
        title = (u["text"][0] if u["text"] else "")
        name = f"{u['id']} · {title if len(title) <= 40 else title[:39] + '…'}" if title else u["id"]
        src = {"kind": spec["kind"], "root": root_rel, "unit": u["id"], "fps": _num(fps), "unit_start_frame": u["start"], "frames": max(1, int(round((end - start) * fps))), "media_start_frame": 0,
               "list": os.path.relpath(path, folder).replace(os.sep, "/")}
        if u["file"]:
            src["file"] = u["file"]
        if spec["render"] and (u["file"] or spec["kind"] == "cinecode"):
            src["render"] = spec["render"]
        layers.append({"type": "video", "id": "shot-" + re.sub(r"[^A-Za-z0-9_-]+", "-", u["id"]), "name": name, "group": "Shots",
                       "start": _num(start), "end": _num(end), "src": vsrc, "src_in": _num(start), "fit": "cover",
                       "muted": use_mix, "source": src})
        last_end = max(last_end, end)
    if not layers:
        raise SceneImportError("no usable scenes (every scene was empty or outside the video)")
    if elements:
        _wire_elements(layers, units, spec, folder, out, W, H, step, warns, log)
    all_layers = layers + alayers
    duration = max(last_end, max((l["end"] for l in alayers), default=0))
    name = spec["title"] or os.path.basename(folder)
    notes_md = os.path.join(out, "scenes.md")
    proj = schema.normalize({"version": 2, "name": name[:80], "width": W, "height": H, "fps": _num(fps) if fps == int(fps) else fps,
                             "duration": _num(duration), "background": "#000000", "output": "output.mp4", "layers": all_layers, "notes": []})
    # `source` is the contract field of the code-source feature; put it back by hand when this checkout's normalize drops it
    by_id = {l["id"]: l for l in layers}
    for l in proj["layers"]:
        if l.get("id") in by_id and "source" not in l:
            l["source"] = by_id[l["id"]]["source"]
    _write_atomic(pj, schema.dumps(proj))
    _write_atomic(notes_md, _scenes_md(spec, units, name))
    for w in warns:
        log(f"warning: {w}")
    proj["_warnings"] = warns
    return proj


def _scenes_md(spec, units, name):
    lines = [f"# {name}: scenes", "", "Written by `kurgu import`. One video layer per scene; each layer's `source` says where the scene comes from.", ""]
    for u in units:
        lines.append(f"## {u['id']}  (frames {u['start']}-{u['end']})")
        if u["file"]:
            lines.append(f"- file: {u['file']}")
        if u["description"]:
            lines.append(f"- description: {u['description']}")
        if u["text"]:
            lines.append("- text on screen: " + " / ".join(u["text"]))
        if u["vo"]:
            lines.append(f"- voice-over: {u['vo']}")
        lines.append("")
    if spec.get("film_text"):
        lines += ["## Film-wide text", "- " + " / ".join(spec["film_text"]), ""]
    return "\n".join(lines)


def refresh_scenes_md(project_dir, root, list_path):
    """Rewrite the project's scenes.md (the readable scene table `kurgu import` wrote) from the scene list as it is NOW. Only when scenes.md exists."""
    target = os.path.join(project_dir, "scenes.md")
    if not os.path.isfile(target) or os.path.islink(target):
        return False
    spec = read_cinecode(root, list_path) if os.path.basename(list_path).lower() == "shots.json" else read_scenes(root, list_path)
    units = sorted(spec["units"], key=lambda u: (u["start"], u["id"]))
    _write_atomic(target, _scenes_md(spec, units, spec["title"] or os.path.basename(root)))
    return True


def _write_atomic(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = kurgu_paths.tmp_path(path, ".part")
    with kurgu_paths.excl_open(tmp) as f:
        f.write(text)
    os.replace(tmp, path)


def _wire_elements(layers, units, spec, folder, out, W, H, step, warns, log):
    """Run `kurgu elements` for each scene whose file is an html page and point source.elements at the sidecar."""
    import elements as el
    by_unit = {u["id"]: u for u in units}
    todo = [l for l in layers if (l["source"].get("file") or "").lower().endswith((".html", ".htm"))]
    if not todo:
        warns.append("--elements: no scene has an html page to measure")
        return
    try:
        browser = el.Browser(W, H, root=folder)
    except el.ElementsError as e:
        warns.append(f"--elements skipped: {e}")
        return
    try:
        for l in todo:
            u = by_unit[l["source"]["unit"]]
            page = os.path.join(folder, l["source"]["file"])
            params = "&".join(p for p in (spec.get("page_params"), f"w={W}", f"h={H}") if p)
            fr = list(range(u["start"], u["end"], max(1, int(step))))
            fr = sorted(set(fr + [u["end"] - 1])) if u["end"] - 1 >= u["start"] else fr
            local = spec["kind"] != "cinecode"      # cinecode: one film page, frames are film frames. A scenes.json page is the scene's own, drawn from frame 0: shifted to absolute frames
            if local:
                fr = [f - u["start"] for f in fr]
            try:
                res = el.measure(page, fr, size=(W, H), fps=spec["fps"], params=params, browser=browser, offset=u["start"] if local else 0)
            except el.ElementsError as e:
                warns.append(f"{u['id']}: elements not measured ({e})")
                continue
            rel = "elements/" + re.sub(r"[^A-Za-z0-9_-]+", "-", u["id"]) + ".kurgu.json"
            el.clip_to_frames(res, u["start"], u["end"])          # this unit's map holds this unit's frames only
            el.write_map(os.path.join(out, rel), res)
            l["source"]["elements"] = rel
            for w in res.get("warnings", []):
                warns.append(f"{u['id']}: {w}")
    finally:
        browser.close()


# ---------------------------------------------------------------- CLI
def _diag_import(out, msg, level="warn"):
    """Record an import problem in the target project's diagnostics log (kurgu report), when that project exists. Never raises."""
    try:
        out = os.path.realpath(os.path.expanduser(out))
        if os.path.isfile(os.path.join(out, "project.json")):
            import diag
            diag.event(out, "import", "import failed" if msg.startswith("import failed") else "import warning", level, ctx={"detail": msg})
    except Exception:
        pass


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="kurgu import", description=(
        "Build a Kurgu project from the scene list of a video made with code: a cinecode film folder (shots.json + renders/*.mp4), "
        "or any folder with a scenes.json. One video layer per scene, cut at its frame bounds, each with a `source`. "
        "A Remotion project is recognised and answered with what to write instead."))
    ap.add_argument("folder", help="the film / project folder to read (it is never modified)")
    ap.add_argument("--out", default=None, help="Kurgu project folder to create (default: <folder>/kurgu)")
    ap.add_argument("--video", default=None, help="use this rendered mp4 instead of the newest one the folder points at")
    ap.add_argument("--elements", action="store_true", help="also measure text / object boxes of every scene that is an html frame-function page (needs Chrome)")
    ap.add_argument("--elements-step", type=int, default=DEFAULT_STEP, help=f"measure every N-th frame of a scene (default {DEFAULT_STEP})")
    ap.add_argument("--copy", action="store_true", help="copy the video and audio into <out>/media instead of referencing them where they are")
    ap.add_argument("--no-trust", action="store_true", help="do not add the folder to render_trusted_roots in your user config (then `kurgu rerender` refuses sources outside the project)")
    ap.add_argument("--force", action="store_true", help="replace an existing project.json in --out")
    a = ap.parse_args(argv)
    try:
        proj = build(a.folder, out=a.out, video=a.video, elements=a.elements, step=a.elements_step, force=a.force, copy=a.copy)
    except SceneImportError as e:
        print(f"error: {e}", file=sys.stderr)
        _diag_import(a.out or os.path.join(os.path.expanduser(a.folder), "kurgu"), "import failed: " + str(e))
        return 1
    out = os.path.realpath(os.path.expanduser(a.out)) if a.out else os.path.join(os.path.realpath(os.path.expanduser(a.folder)), "kurgu")
    for w in proj.get("_warnings") or []:
        _diag_import(out, w)
    vids = [l for l in proj["layers"] if l["type"] == "video"]
    auds = [l for l in proj["layers"] if l["type"] == "audio"]
    print(out)
    print(f"{len(vids)} scene layers" + (f", {len(auds)} audio layers" if auds else ", sound from the video") + f", {proj['duration']} s at {proj['fps']} fps. "
          f"Open it with: kurgu {out}", file=sys.stderr)
    if not a.no_trust:
        import render_adaptors
        ok, info = render_adaptors.trust_root(a.folder, out)
        if ok:
            print(f"trusted for `kurgu rerender`: {os.path.realpath(os.path.expanduser(a.folder))} added to render_trusted_roots in {info} (undo: remove it there, or import with --no-trust)", file=sys.stderr)
        else:
            print(f"warning: could not add the folder to render_trusted_roots ({info}); `kurgu rerender` will ask for it", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
