@@ block
Moving or placing: x/y is the layer's ANCHOR (text: block centre, even with `align=left`), not its edge. From the note's `anchor` and `bounds` (px) compute x/y so edges line up with nearby content; keep the box in frame.
@@ wake
Taşıma/yerleştirmede katmanın anchor ve bounds değerlerinden konumu hesapla, kenarları yakındaki içerikle hizala;
@@ full
**Placing or moving something for a note.** Each note lists the layers then on screen with `bounds` (`left/top/right/bottom/w/h`, project px, the rendered box) and an `anchor` line saying what the layer's x/y mean. For a text layer x/y is the block CENTRE even with `align=left` (align only aligns lines inside the block), so the left edge is `x - w/2`. Compute x/y from the anchor and bounds so an edge lines up with nearby content: another layer's bounds, or something visible in the note frame (text burned into a video clip is not a layer, so read its position off the picture; `kurgu detect` gives approximate boxes). Keep the box inside the frame.
@@ tool
To move or place something: compute x/y from anchor + bounds, align edges with nearby content, check with render_frame.
