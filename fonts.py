"""Kurgu font registry (SPEC.md §4), shared by server.py and render.py.

Faces come from three sources, highest priority first: the project (`<project>/fonts`), the bundled OFL set
(`<repo>/fonts`) and the operating system. Each face is
    {"id", "family", "style", "weight", "italic", "source": "project"|"bundled"|"system", "file", "index"}
id = ASCII slug of "family style" ("inter-bold", "gill-sans-light"). On an id collision the higher-priority
source wins; inside one source the first file in scan order wins.

Faces that live inside .ttc collections are extracted with fontTools to <user cache>/kurgu/faces/<id>.ttf so the browser
can load exactly the face the renderer uses (`face_file()`).

Decisions where the spec is silent:
- Font names starting with "." (macOS hidden UI fonts) are skipped.
- Only .ttf .otf .ttc .otc are scanned (browsers cannot use .dfont / .pfb; woff is rejected at import).
- A face extracted from a collection keeps `.otf` if it has CFF outlines, otherwise `.ttf`.
- The system scan cache is keyed by the mtimes of every font directory (recursively); override the cache
  location with the KURGU_CACHE environment variable (used by the tests).
"""
import json
import logging
import os
import sys

import kurgu_paths
import threading

from fontTools.ttLib import TTCollection, TTFont, TTLibError

from schema import slugify_font_id

HERE = os.path.dirname(os.path.realpath(__file__))
BUNDLED_DIR = os.path.join(kurgu_paths.data_root(), "fonts")
FALLBACK_ID = "inter-regular"
FONT_EXTS = (".ttf", ".otf", ".ttc", ".otc")
COLLECTION_EXTS = (".ttc", ".otc")
CACHE_VERSION = 2
SOURCE_ORDER = {"project": 0, "bundled": 1, "system": 2}

logging.getLogger("fontTools").setLevel(logging.ERROR)   # silence timestamp warnings from system fonts

_lock = threading.RLock()
_file_cache = {}      # (path, mtime_ns, size) -> [face, ...]
_cmap_cache = {}      # (file, index) -> frozenset of code points
_system_mem = {"stamp": None, "faces": None}


# ---------------------------------------------------------------- locations
def cache_dir():
    return kurgu_paths.cache_dir()


def system_dirs():
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        dirs = ["/System/Library/Fonts", "/Library/Fonts", os.path.join(home, "Library", "Fonts")]
    elif sys.platform.startswith("win"):
        dirs = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
                os.path.join(os.environ.get("LOCALAPPDATA", os.path.join(home, "AppData", "Local")),
                             "Microsoft", "Windows", "Fonts")]
    else:
        dirs = ["/usr/share/fonts", "/usr/local/share/fonts", os.path.join(home, ".local", "share", "fonts"),
                os.path.join(home, ".fonts")]
    return [d for d in dirs if os.path.isdir(d)]


def project_fonts_dir(project_dir):
    return os.path.join(project_dir, "fonts") if project_dir else None


# ---------------------------------------------------------------- reading one font file
def _name(font, *ids):
    try:
        table = font["name"]
    except KeyError:
        return ""
    for i in ids:
        for plat, enc, lang in ((3, 1, 0x409), (1, 0, 0), (3, 1, None)):
            for rec in table.names:
                if rec.nameID == i and rec.platformID == plat and rec.platEncID == enc \
                        and (lang is None or rec.langID == lang):
                    try:
                        s = rec.toUnicode().strip()
                    except Exception:
                        continue
                    if s:
                        return s
    return ""


def _face_from_font(font, path, index, source):
    family = _name(font, 16, 1)
    style = _name(font, 17, 2) or "Regular"
    if not family or family.startswith("."):
        return None
    try:
        weight = int(font["OS/2"].usWeightClass)
    except Exception:
        weight = 400
    italic = False
    try:
        italic = bool(font["OS/2"].fsSelection & 1) or bool(font["head"].macStyle & 2)
    except Exception:
        pass
    if "italic" in style.lower() or "oblique" in style.lower():
        italic = True
    fid = slugify_font_id(family, style)
    if not fid:
        return None
    return {"id": fid, "family": family, "style": style, "weight": weight, "italic": italic,
            "source": source, "file": os.path.abspath(path), "index": index}


def scan_font_file(path, source="project"):
    """All faces in one font file (a .ttc yields several). Broken files yield []."""
    try:
        st = os.stat(path)
    except OSError:
        return []
    key = (path, st.st_mtime_ns, st.st_size)
    with _lock:
        hit = _file_cache.get(key)
    if hit is not None:
        return [dict(f, source=source) for f in hit]
    faces = []
    try:
        if path.lower().endswith(COLLECTION_EXTS):
            coll = TTCollection(path, lazy=True)
            for i, font in enumerate(coll.fonts):
                f = _face_from_font(font, path, i, source)
                if f:
                    faces.append(f)
            coll.close()
        else:
            font = TTFont(path, lazy=True)
            f = _face_from_font(font, path, 0, source)
            if f:
                faces.append(f)
            font.close()
    except (TTLibError, OSError, Exception):
        faces = []
    with _lock:
        _file_cache[key] = faces
    return [dict(f, source=source) for f in faces]


def _scan_dir(directory, source, recursive):
    faces = []
    if not directory or not os.path.isdir(directory):
        return faces
    if recursive:
        files = []
        for root, dirs, names in os.walk(directory, followlinks=True):
            dirs.sort()
            files += [os.path.join(root, n) for n in sorted(names)]
    else:
        files = [os.path.join(directory, n) for n in sorted(os.listdir(directory))]
    for p in files:
        if p.lower().endswith(FONT_EXTS) and not os.path.basename(p).startswith("."):
            faces += scan_font_file(p, source)
    return faces


# ---------------------------------------------------------------- system scan (disk cache)
def _stamp(dirs):
    stamp = []
    for d in dirs:
        for root, subdirs, _ in os.walk(d, followlinks=True):
            subdirs.sort()
            try:
                stamp.append([root, os.stat(root).st_mtime_ns])
            except OSError:
                pass
    return stamp


def system_faces():
    dirs = system_dirs()
    stamp = _stamp(dirs)
    with _lock:
        if _system_mem["stamp"] == stamp and _system_mem["faces"] is not None:
            return _system_mem["faces"]
    path = os.path.join(cache_dir(), "fonts.json")
    faces = None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") == CACHE_VERSION and data.get("stamp") == stamp:
            faces = data["faces"]
    except (OSError, ValueError, KeyError):
        pass
    if faces is None:
        faces = []
        for d in dirs:
            faces += _scan_dir(d, "system", True)
        try:
            tmp = path + f".tmp{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": CACHE_VERSION, "stamp": stamp, "faces": faces}, f)
            os.replace(tmp, path)
        except OSError:
            pass
    with _lock:
        _system_mem.update(stamp=stamp, faces=faces)
    return faces


# ---------------------------------------------------------------- registry
def bundled_faces():
    return _scan_dir(BUNDLED_DIR, "bundled", False)


def project_faces(project_dir):
    return _scan_dir(project_fonts_dir(project_dir), "project", False)


def registry(project_dir=None):
    """Winning face per id (project > bundled > system), sorted by family, weight, style."""
    by_id = {}
    for group in (project_faces(project_dir) if project_dir else [], bundled_faces(), system_faces()):
        for f in group:
            by_id.setdefault(f["id"], f)
    return sorted(by_id.values(), key=lambda f: (SOURCE_ORDER[f["source"]], f["family"].lower(), f["weight"],
                                                 f["italic"], f["style"].lower()))


def list_faces(project_dir=None):
    return registry(project_dir)


def get_face(font_id, project_dir=None):
    """The face for `font_id`, or None. Checks project, then bundled, then system (system scan only if needed)."""
    for group in (project_faces(project_dir) if project_dir else [], bundled_faces()):
        for f in group:
            if f["id"] == font_id:
                return f
    for f in system_faces():
        if f["id"] == font_id:
            return f
    return None


def refresh():
    """Forget in-memory caches (after a font was imported)."""
    with _lock:
        _file_cache.clear()
        _cmap_cache.clear()


# ---------------------------------------------------------------- standalone face files
def face_file(face):
    """Path of a standalone .ttf/.otf for `face`. Collection members are extracted to the cache (once)."""
    path = face["file"]
    if not path.lower().endswith(COLLECTION_EXTS):
        return path
    src_mtime = os.stat(path).st_mtime_ns
    base = os.path.join(cache_dir(), "faces")
    os.makedirs(base, exist_ok=True)
    for ext in (".ttf", ".otf"):
        cand = os.path.join(base, face["id"] + ext)
        if os.path.exists(cand) and os.stat(cand).st_mtime_ns == src_mtime:
            return cand
    with _lock:
        coll = TTCollection(path, lazy=False)
        try:
            font = coll.fonts[face["index"]]
            ext = ".otf" if font.sfntVersion == "OTTO" else ".ttf"
            target = os.path.join(base, face["id"] + ext)
            tmp = target + f".tmp{os.getpid()}{threading.get_ident()}"
            font.save(tmp)
        finally:
            coll.close()
        os.replace(tmp, target)
        os.utime(target, ns=(src_mtime, src_mtime))
    return target


def mime_for(path):
    return "font/otf" if path.lower().endswith(".otf") else "font/ttf"


# ---------------------------------------------------------------- glyph coverage / PIL loading
def cmap(face):
    """frozenset of code points the face can draw."""
    key = (face["file"], face["index"])
    with _lock:
        hit = _cmap_cache.get(key)
    if hit is None:
        try:
            if face["file"].lower().endswith(COLLECTION_EXTS):
                coll = TTCollection(face["file"], lazy=True)
                font = coll.fonts[face["index"]]
                hit = frozenset(font.getBestCmap() or {})
                coll.close()
            else:
                font = TTFont(face["file"], lazy=True)
                hit = frozenset(font.getBestCmap() or {})
                font.close()
        except Exception:
            hit = frozenset()
        with _lock:
            _cmap_cache[key] = hit
    return hit


def load_pil_font(font_id, size, project_dir=None):
    """-> (ImageFont, face, found). Unknown ids fall back to the bundled inter-regular (found=False)."""
    from PIL import ImageFont
    face = get_face(font_id, project_dir)
    found = face is not None
    if face is None:
        face = get_face(FALLBACK_ID, project_dir)
    if face is None:                        # bundled fonts missing: last resort
        return ImageFont.load_default(size), None, False
    try:
        return ImageFont.truetype(face["file"], size, index=face["index"]), face, found
    except OSError:
        return ImageFont.load_default(size), face, False
