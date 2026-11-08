@@ block
Burned-in footage (no layer or element map): `kurgu detect <dir> <sec>` (MCP `detect`) gives approximate boxes.
@@ wake
Klibe gömülü yazı/şekil için `kurgu detect {dir} <sn>` yaklaşık kutuları verir.
@@ full
**Burned-in text and shapes.** Pixels Kurgu did not draw (text or shapes in footage, 3D or other tools' renders) have no layer `bounds` and no element map. `kurgu detect <dir> <seconds> [--layer ID]` (alias `kurgu ocr`; MCP `detect`) finds text lines and shapes with boxes in PROJECT px. They are approximate (`approx: true`): still look at the frame before aligning anything to them.
