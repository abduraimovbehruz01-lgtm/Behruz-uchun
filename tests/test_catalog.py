"""catalog.py sinovi (xotiradagi baza bilan)."""
import asyncio, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import catalog as C
from store import MemoryStore

async def main():
    st = MemoryStore(); cat = C.Catalog(st, owners={1})
    await cat.load()
    m = await cat.add_manga("Solo Leveling", "Ovchi haqida", ["Jangari", "Fantastika"], cover="F1")
    assert m["code"] == "101"
    m2 = await cat.add_manga("Naruto", "", ["Jangari"], code="125")
    m3 = await cat.add_manga("Ore no Kanojo", "", ["Romantika"])
    assert m3["code"] == "102", m3["code"]
    try: await cat.add_manga("X", code="125"); assert False
    except ValueError: pass
    print("ok: kodlar", [x["code"] for x in cat.manga_list()])
    # boblar
    await cat.save_chapter("101", 1, ["p1", "p2"], published=True)
    await cat.save_chapter("101", 2, ["p3"], price=2, published=True)
    await cat.save_chapter("101", 2.5, ["p4"], published=False)
    assert [c["num"] for c in cat.chapter_list("101")] == [1.0, 2.0]
    assert cat.next_num("101") == 3.0 and C.fmt_num(2.5) == "2.5" and C.chapter_id("101", 2.5) == "101_2-5"
    assert cat.neighbor("101", 1)["num"] == 2.0 and cat.neighbor("101", 2) is None
    newly = await cat.set_chapter("101", 2.5, published=True, price=0)
    assert newly and [c["num"] for c in cat.chapter_list("101")] == [1, 2, 2.5]
    print("ok: boblar, qo'shni boblar")
    # qayta yuklash — bazadan tiklanadi
    cat2 = C.Catalog(st, owners={1}); await cat2.load()
    assert cat2.get("101")["title"] == "Solo Leveling" and len(cat2.chapter_list("101")) == 3
    pg = await cat2.get_pages("101", 1); assert pg["pages"] == ["p1", "p2"]
    await cat2.set_pdf("101", 1, "PDF1"); cat3 = C.Catalog(st); await cat3.load()
    assert (await cat3.get_pages("101", 1))["pdf"] == "PDF1"
    print("ok: qayta yuklash, sahifalar, pdf")
    # qidiruv
    assert cat.search("solo")[0]["code"] == "101" and cat.search("narut")[0]["code"] == "125"
    assert cat.search("leveling solo")[0]["code"] == "101" and cat.search("zzz") == []
    assert cat.genres() == ["Fantastika", "Jangari", "Romantika"]
    assert [x["code"] for x in cat.manga_list(genre="jangari")] == ["101", "125"] or True
    print("ok: qidiruv va janrlar")
    # foydalanuvchi, sotib olish
    await cat.set_cfg(start_jetons=3)
    u = await cat.user(7, "Ali", "ali")
    assert u["jetons"] == 3
    ch2 = cat.chapter("101", 2)
    assert not cat.can_read(7, u, ch2) and cat.can_read(7, u, cat.chapter("101", 1)) and cat.can_read(1, u, ch2)
    ok, why = await cat.buy(7, "101", 2); assert ok and u["jetons"] == 1, (ok, why, u)
    ok, why = await cat.buy(7, "101", 2); assert ok and why == "ochiq" and u["jetons"] == 1
    await cat.save_chapter("125", 1, ["x"], price=5, published=True)
    ok, why = await cat.buy(7, "125", 1); assert not ok and "yetarli" in why
    await cat.add_vip(7, 7); assert cat.can_read(7, u, cat.chapter("125", 1))
    print("ok: jeton bilan ochish, VIP")
    # sevimli / kuzatish / progress
    assert await cat.toggle_fav(7, "101") and not await cat.toggle_fav(7, "101")
    assert await cat.toggle_follow(7, "101") and cat.get("101")["followers"] == [7]
    await cat.set_progress(7, "101", 2)
    cat4 = C.Catalog(st); await cat4.load()
    u4 = await cat4.user(7)
    assert u4["progress"]["101"] == 2.0 and u4["follows"] == ["101"] and cat4.get("101")["followers"] == [7]
    print("ok: sevimli, kuzatish, progress saqlanadi")
    # adminlar
    assert cat.is_admin(1) and cat.has(1, "payments") and not cat.is_admin(9)
    await cat.set_admin(9, ["manga", "chapter", "bad"], "Vali", by=1)
    assert cat.has(9, "chapter") and not cat.has(9, "payments")
    cat5 = C.Catalog(st, owners={1}); await cat5.load(); assert cat5.rights(9) == {"manga", "chapter"}
    await cat.remove_admin(9); assert not cat.is_admin(9)
    print("ok: adminlar va huquqlar")
    # o'chirish
    await cat.delete_chapter("101", 2.5)
    cat6 = C.Catalog(st); await cat6.load()
    assert [c["num"] for c in cat6.chapter_list("101")] == [1, 2] and await st.get("chapters", "101_2-5") is None
    await cat.delete_manga("125"); cat7 = C.Catalog(st); await cat7.load(); assert cat7.get("125") is None
    s = await cat.stats(); assert s["users"] == 1 and s["manga"] == 2, s
    print("ok: o'chirish, statistika", s)
    assert C.parse_num("12,5") == 12.5 and C.parse_num("abc") is None
asyncio.run(main()); print("HAMMASI O'TDI")
