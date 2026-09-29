"""Word timings -> caption chunks -> Kurgu `text` layers. Pure functions (no I/O); used by mcp_server.make_captions.

Chunking: a new caption starts when the chunk has `max_words`, when adding the word would pass `max_chars`, after
sentence-ending punctuation, or after a pause longer than `gap_break` seconds. Each caption shows from its first word's
start until the next caption starts (when the gap is small) or a short hold after its last word.
"""
import re

SENTENCE_END = re.compile(r"[.!?…。！？]['\")\]”]*$")
ID_RE = re.compile(r"^cap_(\d+)$")


def is_vertical(width, height):
    return height > width


def defaults(width, height):
    """Per-aspect defaults. Vertical: 3 words, lower-middle (62-70% height, above the app UI of TikTok/Reels/Shorts)."""
    if is_vertical(width, height):
        return {"max_words_per_line": 3, "max_chars": 20, "size": round(width * 0.078), "y": 0.66}
    return {"max_words_per_line": 7, "max_chars": 42, "size": round(width * 0.033), "y": 0.86}


def chunk_words(words, max_words=3, max_chars=20, gap_break=0.6):
    chunks, cur = [], []
    for w in words:
        if cur:
            joined = len(" ".join(x["word"] for x in cur)) + 1 + len(w["word"])
            if len(cur) >= max_words or joined > max_chars or w["start"] - cur[-1]["end"] > gap_break:
                chunks.append(cur)
                cur = []
        cur.append(w)
        if SENTENCE_END.search(w["word"]):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def chunk_times(chunks, hold=0.12, join_gap=0.35, min_len=0.2):
    """-> [(start, end)] per chunk, never overlapping, small gaps closed so captions do not flicker."""
    out = []
    for i, c in enumerate(chunks):
        s, e = c[0]["start"], c[-1]["end"]
        nxt = chunks[i + 1][0]["start"] if i + 1 < len(chunks) else None
        e = nxt if nxt is not None and nxt - e <= join_gap else e + hold
        if nxt is not None:
            e = min(e, nxt)
        e = max(e, s + min_len) if nxt is None else max(min(e, nxt), s + 0.05)
        out.append((round(s, 3), round(e, 3)))
    return out


def upper(t, language=""):
    if (language or "").startswith("tr"):
        t = t.replace("i", "İ").replace("ı", "I")
    return t.upper()


def next_index(existing_ids):
    n = [int(m.group(1)) for i in existing_ids if (m := ID_RE.match(str(i)))]
    return max(n) + 1 if n else 1


def build_layers(words, width, height, first_index=1, max_words=None, max_chars=None, font="inter-bold", size=None,
                 y=None, uppercase=False, color="#ffffff", group="Captions", language=""):
    """-> list of text layer dicts (ids cap_<n>, n from first_index), in time order."""
    d = defaults(width, height)
    max_words = int(max_words or d["max_words_per_line"])
    max_chars = int(max_chars or d["max_chars"])
    size = size or d["size"]
    y = d["y"] if y is None else y
    if 0 < y <= 1:
        y = y * height                       # fraction of the height; larger values are pixels
    chunks = chunk_words(words, max_words, max_chars)
    layers = []
    for i, ((s, e), c) in enumerate(zip(chunk_times(chunks), chunks)):
        t = " ".join(w["word"] for w in c)
        layers.append({"id": f"cap_{first_index + i}", "type": "text", "name": t[:40], "group": group, "start": s, "end": e,
                       "text": upper(t, language) if uppercase else t, "font": font, "size": size, "color": color,
                       "x": width / 2, "y": round(y, 1), "align": "center",
                       "shadow": {"enabled": True, "blur": 6, "opacity": 0.9}})
    return layers
