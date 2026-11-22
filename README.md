# Kino-video bot

Telegram bot: kinoni yuborasiz → bot 1 daqiqalik o‘zbekcha hikoya matnini yozadi → siz o‘qib, ovozli xabar yuborasiz → bot sahnalarni qirqib, rang berib, ovozingiz bilan tayyor video yuboradi.

## Qanday ishlaydi

1. Kinoni botga fayl qilib yuborasiz yoki kanaldan forward qilasiz (2 GB gacha).
2. Bot kino nomini va kodini so‘raydi. Fayl izohiga `Nomi, 125` deb yozsangiz, so‘ramaydi.
3. Gemini kinoning ovozini «eshitib», muhim lahzalarni vaqti bilan topadi va ~55 soniyalik hikoya matnini yozadi.
4. Matn oxiriga avtomatik qo‘shiladi: «Kinoni ko‘rmoqchi bo‘lsangiz, profilimdagi botga 125 kodini yuboring.»
5. Siz matnni o‘qib, ovozli xabar yuborasiz. Har gapdan keyin bir lahza to‘xtasangiz, sahnalar aniqroq mos tushadi.
6. Bot har gapga mos sahnalarni oladi: har biri ~2 soniya, boshida tezroq almashadi, sahna almashgan joydan boshlanadi.
7. Video namunadek bo‘ladi: kvadrat 720×720, qoraytirilgan sovuq rang, vinyetka, pastda `@behruzfilmbot` yozuvi, oxirgi gapda «KOD: 125».
8. Ovoz aralashtiriladi: sizning ovozingiz baland, kinoning ovozi orqada past eshitiladi, siz gapirganda yanada pasayadi. Balandlik Instagram me’yoriga (−14 LUFS) keltiriladi.
9. Tayyor videoni yuboradi. «📐 9:16» tugmasi Reels uchun vertikal variantini yasaydi.

Matnni o‘zgartirmoqchi bo‘lsangiz, yangi matnni yozib yuborasiz. «🔄 Boshqa matn» tugmasi boshqa variant yozdiradi. Yangi ovoz yuborsangiz, video qaytadan yig‘iladi.

## Musiqa

Botga qo‘shiq (mp3, m4a…) yuborsangiz, u saqlanadi: Gemini qo‘shiqni tinglab kayfiyatini (sirli, dramatik, quvnoq…) va qaysi janrdagi kinolarga mosligini yozib qo‘yadi. Har yangi videoda bot kino va matnga eng mos qo‘shiqni o‘zi tanlaydi (kam ishlatilganini afzal ko‘radi) va uni ovozingiz ostida past qilib qo‘shadi. Siz gapirganda musiqa pasayadi.
Video ostida: «🎵 Boshqa musiqa», «🔇 Musiqasiz». `/musiqa` — ro‘yxat, o‘chirish, musiqani yoqish/o‘chirish.
Qo‘shiqlar Telegram’da, ro‘yxat esa bot chatidagi qadalgan «musiqalar.json» faylida saqlanadi — server qayta ishga tushsa ham yo‘qolmaydi.

## Instagram tavsifi

Video bilan birga alohida xabarda tavsif keladi: kino nomisiz 2–3 gap, kod qatori va heshteglar.

Buyruqlar: `/start`, `/musiqa`, `/kod 125`, `/bekor`.

## Sozlamalar (Render → Environment)

| Nomi | Nima | Majburiy |
|---|---|---|
| `BOT_TOKEN` | @BotFather bergan token | ha |
| `API_ID`, `API_HASH` | my.telegram.org → API development tools. 20 MB dan katta fayllar uchun | ha |
| `GEMINI_API_KEY` | aistudio.google.com kaliti | ha |
| `OWNER_IDS` | Botdan faqat shu Telegram ID lar foydalanadi (vergul bilan) | tavsiya |
| `WATERMARK` | Videodagi yozuv (standart `@behruzfilmbot`) | yo‘q |
| `CTA_TEXT` | Oxirgi gap, `{kod}` o‘rniga kod qo‘yiladi | yo‘q |
| `GEMINI_MODEL` | Modellar ro‘yxati, vergul bilan (birinchisi ishlamasa keyingisi) | yo‘q |
| `FINAL_PRESET` | x264 tezligi: `veryfast` (standart) yoki `superfast` | yo‘q |

## Server

Render’da Docker xizmat sifatida ishlaydi. Bepul tarif (0.1 CPU) da 2 soatlik kino uchun video taxminan 10–20 daqiqada tayyor bo‘ladi. Starter tarifi (0.5 CPU, ~$7/oy) bir necha barobar tezroq ishlaydi.
Bepul tarifda server 15 daqiqa jim tursa uxlaydi. Ish ketayotganda bot o‘zini uyg‘oq ushlab turadi. Kino fayli faqat ish davomida saqlanadi, keyin o‘chiriladi.
