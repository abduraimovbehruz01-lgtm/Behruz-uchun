"""typeset.py sinovi: soxta sahifalarda matn o'chirilib, tarjima sig'dirib yozilishi."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import numpy as np
import typeset, samples

for name, (img, items) in {"yapon": samples.page_jp(), "yapon ±10%": samples.page_jp(jitter=0.10, seed=7),
                           "ingliz": samples.page_en()}.items():
    blocks = typeset.blocks_from_model(items, *img.size)
    out, st = typeset.render(img, blocks)
    assert st.drawn == len(blocks) and st.forced == 0, (name, st)
    assert out.size == img.size
    print(f"ok: {name}: {st.drawn} ta blok")
assert typeset.normalize_uz("o'zbek ma'no gʻalaba  ") == "o‘zbek ma’no g‘alaba"
wt, _ = samples.webtoon()
segs = typeset.split_points(wt)
assert len(segs) >= 4 and segs[0][0] == 0 and segs[-1][1] == wt.height
print("ok: webtoon bo'laklari", len(segs))
print("HAMMASI O'TDI")
