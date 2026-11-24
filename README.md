# Manga tarjimon bot

Telegram bot: manga, manhva yoki manxua sahifasini yuborasiz — u matnlarni topadi, **o‘zbek tiliga** tarjima qiladi
va rasmning o‘ziga (pufaklar ichiga) yozib qaytaradi.

## Nima qila oladi
- 📷 **Rasm** → tarjima qilingan rasm. Fayl qilib yuborilsa — fayl qilib qaytaradi (sifat yo‘qolmaydi).
- 🖼 **Albom** → har bir sahifa tartib bilan tarjima qilinadi.
- 📚 **PDF / ZIP / CBZ** (butun bob, 20 MB gacha) → o‘sha formatda qaytaradi: PDF → PDF, ZIP/CBZ → ZIP/CBZ (+ o‘qish uchun PDF).
- Uzun webtoon rasmlarini o‘zi bo‘laklarga bo‘lib tarjima qiladi.
- Har bir sahifa ostida **📝 Matn** tugmasi — asl matn va tarjimasi.
- Kunlik limit, majburiy obuna (ixtiyoriy), adminga `/stat`.

## Qanday ishlaydi
1. `translator.py` — Google Gemini sahifani ko‘radi: har bir pufakning joyi (box_2d), asl matni va tarjimasini qaytaradi.
   Bir nechta model navbat bilan ishlatiladi: biri band yoki bepul limiti tugasa — keyingisi.
2. `typeset.py` — asl harflarni o‘chiradi (pufak chizig‘i va rasmga tegmaydi), pufakning ichki chegarasini topadi
   va o‘zbekcha matnni sig‘adigan eng katta o‘lchamda yozadi. Rastr ustidagi matnga oq plashka qo‘yiladi.
3. `chapters.py` — ZIP/CBZ/PDF ichidan sahifalarni oladi, natijadan PDF va ZIP yasaydi.
4. `bot.py` — Telegram qismi (aiogram 3, Render uchun webhook).

## Sozlamalar (Render → Environment)
| Nomi | Nima | Standart |
|---|---|---|
| `BOT_TOKEN` | @BotFather bergan token | **majburiy** |
| `GEMINI_API_KEY` | aistudio.google.com → Get API key | **majburiy** |
| `OWNER_IDS` | sizning Telegram ID ingiz (limit yo‘q, `/stat`) | — |
| `DAILY_LIMIT` | bir kishiga kuniga nechta sahifa (`0` = cheksiz) | `40` |
| `MAX_PAGES` | bitta fayldan eng ko‘pi nechta sahifa | `60` |
| `REQUIRED_CHANNEL` | majburiy obuna kanali, masalan `@kanalim` (botni kanalga admin qiling) | — |
| `ALLOWED_IDS` | to‘ldirilsa, botdan faqat shu ID lar foydalanadi | — |
| `GEMINI_MODEL` | modellar ro‘yxati, vergul bilan | kodda |
| `PLATE` | `0` — rastr ustidagi matnga oq plashka qo‘ymaslik | `1` |

## Cheklovlar
- Telegram botlarga 20 MB dan katta faylni yuklab olishga ruxsat bermaydi — katta bobni qismlarga bo‘lib yuboring.
- Gemini bepul limiti kunlik; har sahifa = 1 so‘rov (uzun webtoon — bir nechta). Limit kalitga emas, Google loyihasiga bog‘langan.
- Render bepul tarifida server 15 daqiqa ishlatilmasa uxlaydi — birinchi javob ~1 daqiqa kechikadi.

## Kompyuterda sinash
```
pip install -r requirements.txt
BOT_TOKEN=... GEMINI_API_KEY=... python bot.py      # polling rejimi
python tests/test_bot.py && python tests/test_translator.py && python tests/test_chapters.py
```

Shrift: Comic Relief (SIL Open Font License, `fonts/OFL.txt`).
