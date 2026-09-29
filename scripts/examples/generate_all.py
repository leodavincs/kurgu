#!/usr/bin/env python3
"""Regenerate every bundled example / template asset from code (deterministic, no downloads).

    python3 scripts/examples/generate_all.py              # everything: media, projects, thumbnails
    python3 scripts/examples/generate_all.py media        # only clips, music, sfx
    python3 scripts/examples/generate_all.py projects     # only project.json files + shape PNGs
    python3 scripts/examples/generate_all.py thumbs       # only the template thumbnails (renders frames)
    python3 scripts/examples/generate_all.py media --only aurora,lofi_keys   # a subset

Needs numpy, Pillow, fontTools and ffmpeg (the same as Kurgu itself). Takes about 2-4 minutes.
"""
import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _music(name):
    import audio
    return audio.build_music(name)


def _sfx(name):
    import audio
    return audio.build_sfx(name)


def _clip(name):
    import visuals
    return visuals.build_one(name)


def do_media(only):
    import audio
    import visuals
    want = set(only.split(",")) if only else None
    pick = lambda names: [n for n in names if want is None or n in want]
    with ProcessPoolExecutor(min(6, os.cpu_count() or 2)) as ex:
        jobs = ([("video", ex.submit(_clip, n)) for n in pick(visuals.CLIPS)] +
                [("music", ex.submit(_music, n)) for n in pick(audio.MUSIC)] +
                [("sfx", ex.submit(_sfx, n)) for n in pick(audio.SFX)])
        for kind, j in jobs:
            print(f"  {kind}: {j.result()}", flush=True)


def do_projects():
    import build_showcase
    import build_templates
    build_showcase.build()
    build_templates.build_all()
    print("  projects written")


def do_thumbs():
    import build_templates
    build_templates.thumbnails()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", nargs="*", default=["media", "projects", "thumbs"], choices=["media", "projects", "thumbs"])
    ap.add_argument("--only", help="comma separated clip / music / sfx names (media step)")
    a = ap.parse_args()
    for w in a.what:
        print(w + " ...", flush=True)
        {"media": lambda: do_media(a.only), "projects": do_projects, "thumbs": do_thumbs}[w]()
    print("done")


if __name__ == "__main__":
    main()
