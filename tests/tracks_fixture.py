"""A synthetic project shaped like a real one: groups Kartlar 4 / Altyazı 15 / Görüntü 18 (crossfade overlaps) / Ses 3, 75 s. Used by the track view tests and screenshots."""


def _base(kind, i, name, group, start, end, **kw):
    d = {"id": "%s%d" % (kind, i), "name": name, "group": group, "start": round(start, 3), "end": round(end, 3), "x": 960, "y": 540, "scale": 1, "opacity": 1,
         "fade_in": 0, "fade_out": 0, "hidden": False, "locked": False, "effects": []}
    d.update(kw)
    return d


def synthetic(p, n_images=18, n_caps=15):
    layers = []
    for i in range(4):   # cards
        layers.append(_base("card", i, "Kart %d" % (i + 1), "Kartlar", i * 18 + 1, i * 18 + 6, type="text", text="KART %d" % (i + 1), size=120, color="#f1efe9", y=520))
    words = ["Merhaba", "bugün", "size Kurgu'yu", "anlatacağım", "önce", "bir klip", "ekliyoruz", "sonra kesiyoruz", "ve altyazı", "geliyor", "hepsi tek dosyada", "ajanlar da", "aynı dosyayı", "okuyabiliyor", "teşekkürler"]
    for i in range(n_caps):   # subtitles: back to back, one touching pair, one overlapping pair
        s = 1 + i * 4.6
        layers.append(_base("alt", i, words[i % len(words)], "Altyazı", s, s + 4.2 + (0.6 if i == 6 else 0), type="text", text=words[i % len(words)], size=54, color="#ffffff", y=930))
    for i in range(n_images):   # crossfading pictures: each starts 0.5 s before the previous ends
        s = i * 4.0
        if i % 3 == 2:
            layers.append(_base("gor", i, "Klip %d" % (i + 1), "Görüntü", s, s + 4.5, type="video", src="media/a.mp4", src_in=0, muted=True, fit="cover", crop={"top": 0, "bottom": 0, "left": 0, "right": 0}, envelope=[], volume_db=0, fade_in=0.5))
        else:
            layers.append(_base("gor", i, "Görsel %d" % (i + 1), "Görüntü", s, s + 4.5, type="image", src="media/logo.png", fit="cover", crop={"top": 0, "bottom": 0, "left": 0, "right": 0}, fade_in=0.5))
    for i in range(3):
        layers.append({"id": "ses%d" % i, "type": "audio", "name": ["Müzik", "Anlatım", "Efekt"][i], "group": "Ses", "start": [0, 2, 40][i], "end": [75, 38, 46][i], "src": "media/muzik.wav", "src_in": 0,
                       "volume_db": -6, "envelope": [], "fade_in": 0, "fade_out": 0, "hidden": False, "locked": False, "muted": False})
    p["layers"] = layers
    p["duration"] = 75
    p["notes"] = []
    return p
