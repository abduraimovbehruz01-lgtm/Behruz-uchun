# Manhora — manga o‘qish boti

Telegram bot: foydalanuvchilar manga va manhvalarni **o‘zbek tilida** o‘qiydi, adminlar esa mangalarni qo‘shadi va
boblarni yuklaydi (kerak bo‘lsa bot ularni o‘zi tarjima qiladi).

## Foydalanuvchi uchun
- 🔢 Manga **kodi** (masalan `101`) yoki havola `t.me/ManhoraBot?start=101` → manga kartasi.
- 📚 Katalog, 🔥 mashhurlar, 🏷 janrlar, 🔎 nom bo‘yicha qidiruv.
- 📖 Botda o‘qish: sahifalar albom bo‘lib keladi, «Keyingi bob» tugmasi, 📄 PDF qilib olish.
- ❤️ Sevimlilar, 🔔 kuzatish (yangi bob chiqsa xabar), ▶️ qolgan joydan davom ettirish.
- 🔒 Pullik boblar jeton bilan ochiladi (pullik boblar forward/saqlashdan himoyalangan), 💎 VIP.

## Admin uchun (`/admin`)
- Manga qo‘shish (nomi, tavsif, janr, muqova, kod) va tahrirlash, holati (🟢 davom etmoqda / ✅ tugagan /
  ⏸ to‘xtatilgan / 🔜 tez orada), chiqish kunlari, yashirish, o‘chirish.
- Bob qo‘shish: ZIP/CBZ/PDF yoki rasmlar → «tarjima qilinsinmi?» → omborga yuklanadi → ko‘rib chiqib,
  bepul yoki pullik (1–10 🪙) qilib chop etiladi → kuzatuvchilarga xabar boradi.
- **Ko‘p bobni birdan**: ichida bob papkalari bo‘lgan bitta ZIP yoki bir nechta fayl birdan; raqamlar nomidan
  olinadi («Chapter 12», «Ch.012», «12-bob», «第12話»…). Bor boblar o‘tkazib yuboriladi yoki almashtiriladi.
- **20 MB dan katta fayl** — Google Drive, Dropbox yoki Yandex Disk havolasi orqali (500 MB gacha).
- **Takroriy bob**: bir xil bob ikkinchi marta yuklansa, bot to‘xtatib ogohlantiradi (sahifalar «barmoq izi»).
- **Ommaviy chop etish**: «dastlabki N ta bob bepul, qolgani X 🪙», hozir yoki har kuni bittadan.
- **Rejalashtirish**: bob 09:00, 12:00, 15:00, 18:00 yoki 21:00 da (Toshkent) o‘zi chiqadi.
- **Bob tahriri**: rasmlarni ko‘rish, almashtirish, o‘chirish, surish, qo‘shish; bob nomi; narx.
- **Glossariy**: har manga uchun ismlar lug‘ati — tarjimada har doim bir xil yoziladi; tarjimadan keyin bot
  yangi ismlarni o‘zi taklif qiladi.
- **Adminlar** (faqat bosh admin): ID, kontakt yoki forward orqali qo‘shish, har biriga alohida huquqlar.
- **Jurnal**: kim qachon nima qilgani. **Majburiy obuna**: kanallarni paneldan qo‘shish/o‘chirish.
- **🔗 Hamkor manbalar**: ruxsat bergan tarjimon jamoa yoki muallif botni o‘z kanaliga admin qiladi, admin
  kanalni mangaga bog‘laydi (kalit so‘z, tarjima kerakmi, ruxsat izohi, «Tarjima: …» yozuvi). Kanalga tashlangan
  har bir bob — ZIP/CBZ/PDF, rasmlar albomi yoki Telegraph havolasi — o‘zi olinadi, kerak bo‘lsa tarjima qilinadi
  va **bepul** chop etiladi. Bor yoki takroriy bob qayta olinmaydi; tarjima sifati past bo‘lsa — qoralama bo‘lib
  adminga keladi; Gemini limiti tugasa — navbatda kutib turadi; kuniga ko‘pi bilan 20 bob (`auto_daily`).
- **📣 Reklama kanali**: har yangi bob (qo‘lda, rejali yoki hamkor manbadan) chop etilganda kanalga muqova,
  bob raqami, kod va «📖 O‘qish» tugmasi bilan post o‘zi chiqadi. Sinov posti, to‘xtatish, uzish.
- **✍️ Tarjimon yozuvi**: manga yoki bob uchun «Tarjima: … jamoasi» — kartada, o‘qishda va reklama postida ko‘rinadi.
- Botni yangi kanalga qo‘shsangiz, bot kanal nima uchun ekanini so‘raydi: ombor, majburiy obuna, reklama yoki
  hamkor manba. Begona odam botni o‘z kanaliga qo‘shsa — bosh adminga «manba qilib ulash» tugmasi bilan xabar keladi.
- Rasmlar ombori: yopiq kanal oching va botni unga admin qiling — bot o‘zi aniqlaydi.
- Sahifa yoki fayl yuborsangiz — tarjima qilib qaytaradi (tezkor tarjima, faqat adminlar uchun).

## Fayllar
`bot.py` (ishga tushirish), `reader.py` (o‘quvchilar), `admin.py` (admin panel, bob yuklash, chop etish),
`staff.py` (adminlar, jurnal, majburiy obuna, reklama kanali), `editor.py` (bob tahriri, glossariy),
`scheduler.py` (rejadagi boblar), `autosrc.py` (hamkor kanallar), `promo.py` (yangi bob e’loni), `tools.py` (tezkor tarjima),
`catalog.py` (katalog mantiqi), `store.py` (Firebase / xotira), `core.py` (umumiy), `translator.py`, `typeset.py`,
`chapters.py` (tarjima, rasmga yozish, PDF/ZIP).

## Sozlamalar (Render → Environment)
| Nomi | Nima |
|---|---|
| `BOT_TOKEN` | @BotFather tokeni (**majburiy**) |
| `GEMINI_API_KEY` | aistudio.google.com kaliti (**majburiy**) |
| `FIREBASE_SERVICE_ACCOUNT` | Firebase kalit faylining matni (JSON). Bo‘lmasa ma’lumotlar qayta ishga tushganda o‘chadi |
| `OWNER_IDS` | bosh admin(lar) Telegram ID si |
| `REQUIRED_CHANNEL` | majburiy obuna kanali, masalan `@kanalim` (ixtiyoriy) |
| `STORAGE_CHAT` | rasmlar ombori kanali (odatda bot o‘zi aniqlaydi) |

## Tarjima qanday ishlaydi
1. `translator.py` — Google Gemini sahifani ko‘radi: har bir pufakning joyi (box_2d), asl matni va tarjimasini qaytaradi.
   Bir nechta model navbat bilan ishlatiladi: biri band yoki bepul limiti tugasa — keyingisi.
2. `typeset.py` — asl harflarni o‘chiradi (pufak chizig‘i va rasmga tegmaydi), pufakning ichki chegarasini topadi
   va o‘zbekcha matnni sig‘adigan eng katta o‘lchamda yozadi. Rastr ustidagi matnga oq plashka qo‘yiladi.
3. `chapters.py` — ZIP/CBZ/PDF ichidan sahifalarni oladi, natijadan PDF va ZIP yasaydi.
4. `bot.py` — Telegram qismi (aiogram 3, Render uchun webhook).

## Cheklovlar
- Telegram botlarga 20 MB dan katta faylni yuklab olishga ruxsat bermaydi — katta faylni havola qilib yuboring.
  Hamkor kanaldagi 20 MB dan katta fayl ham avtomatik olinmaydi (bot adminga shu haqda yozadi).
- Hamkor manba faqat **ruxsat bilan**: kim, qachon, qanday ruxsat bergani manba kartasida saqlanadi.
- Gemini bepul limiti kunlik; har sahifa = 1 so‘rov (uzun webtoon — bir nechta). Limit kalitga emas, Google loyihasiga bog‘langan.
- Render bepul tarifida server 15 daqiqa ishlatilmasa uxlaydi — birinchi javob ~1 daqiqa kechikadi.
  Rejadagi bob: server uyg‘oq bo‘lsa — aniq vaqtida chiqadi (yaqin 20 daqiqada bob bo‘lsa, bot o‘zini uyg‘oq tutadi);
  uxlab yotgan bo‘lsa — keyingi uyg‘onganda. Aniq vaqtda chiqishi uchun tashqi «uyg‘otuvchi» kerak: har kuni
  03:50, 06:50, 09:50, 12:50, 15:50 (UTC) da `https://manga-tarjima.onrender.com/health` ga murojaat
  (masalan, GitHub Actions yoki cron-job.org).

## Kompyuterda sinash
```
pip install -r requirements.txt
BOT_TOKEN=... GEMINI_API_KEY=... python bot.py      # polling rejimi
for t in tests/test_*.py; do python $t; done
```

Shrift: Comic Relief (SIL Open Font License, `fonts/OFL.txt`).
