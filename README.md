# Kino-video bot

Telegram bot: kinoni yuborasiz → bot 1 daqiqalik o‘zbekcha hikoya matnini yozadi → siz o‘qib, ovozli xabar yuborasiz → bot sahnalarni qirqib, rang berib, ovozingiz bilan tayyor video yuboradi.

## Qanday ishlaydi

1. Kinoni botga fayl qilib yuborasiz yoki kanaldan forward qilasiz (2 GB gacha).
2. Bot kino nomini va kodini so‘raydi. Fayl izohiga `Nomi, 125` deb yozsangiz, so‘ramaydi.
3. Gemini kinoning ovozini «eshitib», qahramonlarni, qisqacha mazmunni va muhim lahzalarni vaqti bilan topadi, so‘ng kinoni ko‘rmagan odam ham tushunadigan hikoya matnini yozadi (standart 2 daqiqa, `/uzunlik` bilan 1–3 daqiqa).
4. Matn oxiriga avtomatik qo‘shiladi: «Kinoni ko‘rmoqchi bo‘lsangiz, profilimdagi botga 125 kodini yuboring.»
5. Siz matnni o‘qib, ovozli xabar yuborasiz. Har gapdan keyin bir lahza to‘xtasangiz, sahnalar aniqroq mos tushadi.
6. Bot har gapga mos sahnalarni oladi: har biri ~2 soniya, boshida tezroq almashadi, sahna almashgan joydan boshlanadi.
7. Video namunadek bo‘ladi: kvadrat 1080×1080 (720p kinodan 720×720), yengil kino rangi va vinyetka, pastda `BehruzFilm` yozuvi, oxirgi gapda «KOD: 125».
8. Ovoz aralashtiriladi: sizning ovozingiz baland, kinoning ovozi orqada past eshitiladi, siz gapirganda yanada pasayadi. Balandlik Instagram me’yoriga (−14 LUFS) keltiriladi.
9. Tayyor videoni yuboradi. «📐 9:16» tugmasi Reels uchun vertikal variantini yasaydi.

Matnni o‘zgartirmoqchi bo‘lsangiz, yangi matnni yozib yuborasiz. «🔄 Boshqa matn» tugmasi boshqa variant yozdiradi. Yangi ovoz yuborsangiz, video qaytadan yig‘iladi.

Buyruqlar: `/start`, `/bekor`, `/kod 125`.

## Sozlamalar (Render → Environment)

| Nomi | Nima | Majburiy |
|---|---|---|
| `BOT_TOKEN` | @BotFather bergan token | ha |
| `API_ID`, `API_HASH` | my.telegram.org → API development tools. 20 MB dan katta fayllar uchun | ha |
| `GEMINI_API_KEY` | aistudio.google.com kaliti | ha |
| `OWNER_IDS` | Botdan faqat shu Telegram ID lar foydalanadi (vergul bilan) | tavsiya |
| `WATERMARK` | Videodagi yozuv (standart `BehruzFilm`) | yo‘q |
| `VIDEO_SIZE` | Eng katta o‘lcham (standart 1080) | yo‘q |
| `VIDEO_LEN` | Standart uzunlik, soniya (120) | yo‘q |
| `CTA_TEXT` | Oxirgi gap, `{kod}` o‘rniga kod qo‘yiladi | yo‘q |
| `GEMINI_MODEL` | Modellar ro‘yxati, vergul bilan (birinchisi ishlamasa keyingisi) | yo‘q |
| `FINAL_PRESET` | x264 tezligi: `veryfast` (standart) yoki `superfast` | yo‘q |

## Server

Render’da Docker xizmat sifatida ishlaydi. Bepul tarif (0.1 CPU) da 2 daqiqalik video taxminan 15–25 daqiqada yig‘iladi (720p kinodan tezroq). Starter tarifi (0.5 CPU, ~$7/oy) 5 barobar tez: 3–5 daqiqa.
Bepul tarifda server 15 daqiqa jim tursa uxlaydi. Ish ketayotganda bot o‘zini uyg‘oq ushlab turadi. Kino fayli faqat ish davomida saqlanadi, keyin o‘chiriladi.
