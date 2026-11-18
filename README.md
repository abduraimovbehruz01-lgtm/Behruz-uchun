# Taqdimot — Telegram bot ichidagi ilova (Web App). iPhone orqali o‘rnatish

Ilova nima qiladi:

- Botga kirgan odam pastdagi **Taqdimot** tugmasini bosadi va ilova Telegram’ning o‘zida ochiladi. Odam Telegram akkaunti bilan avtomatik kiradi, ro‘yxatdan o‘tish shart emas.
- Hisob **jetonlarda**: har bir yangi foydalanuvchiga **3 ta jeton bepul** (sonini admin paneldan o‘zgartirasiz).
- Taqdimot **uch usulda** yasaladi:
  - **💡 Mavzu bo‘yicha** (1 jeton) — mavzuni yozasiz, matnni AI o‘zi yozadi;
  - **📄 O‘z matnimdan** (1 jeton) — mustaqil ish, referat yoki konspekt matnini joylaysiz (yoki Word / .txt faylni tanlaysiz), AI uni slaydlarga bo‘lib beradi va o‘zidan yangi fakt qo‘shmaydi;
  - **🎯 Namunadek** (2 jeton) — namuna taqdimotni yuklaysiz va yangi mavzuni yozasiz:
    - **PowerPoint (.pptx)** namuna — yangi taqdimot namunaning **o‘zidan** yasaladi: fon, shrift, shakllar, jadvallar va joylashuv aynan o‘zidek qoladi, faqat matnlar yangi mavzuga, rasmlar esa mavzuga mos rasmlarga (Wikimedia Commons, bepul) almashadi. Slaydlar sonini foydalanuvchi tanlaydi: «Namunadek» yoki 10/12/15/20 — ko‘p kerak bo‘lsa, namunadagi slayd dizaynlari takrorlanadi. Natija PowerPoint bo‘lib chatga keladi;
    - **PDF yoki Word** namuna — tuzilishi va uslubi o‘xshatiladi, slaydlar ilovaning dizaynlarida yasaladi.
    - Namuna fayli foydalanuvchining bot chatida saqlanadi (bepul), keyingi safar qayta yuklash shart emas.
- **Do‘st taklif qilish:** har kimning o‘z havolasi bor; har 5 ta yangi do‘st uchun +2 jeton (admin paneldan o‘zgartiriladi).
- Tayyor taqdimot **PowerPoint, PDF, Word** yoki **rasmlar (ZIP)** ko‘rinishida **bot chatiga keladi**. Krossvord ham qo‘shsa bo‘ladi.
- Jeton tugagach, foydalanuvchi paket sotib oladi. Ikki yo‘l bor:
  - **📸 Karta orqali:** ilovada paketni tanlaydi → bot unga karta raqamini yuboradi → u chek rasmini botga yuboradi → chek sizga Telegram’da **✅ / ❌** tugmalari bilan keladi → ✅ ni bossangiz, jetonlar avtomatik tushadi va foydalanuvchiga xabar boradi.
  - **⭐ Telegram Stars:** to‘lov Telegram ichida, jeton darhol tushadi, siz hech narsa qilmaysiz.
- **Hammaga xabar** yuborish va **majburiy kanal obunasi** (ixtiyoriy) ham bor.
- **Admin panel** ham Telegram’da ochiladi: botga `/admin` yozasiz va **login va parol** bilan kirasiz.

**Kerak bo‘ladi:** iPhone (Safari va Telegram), Gmail akkaunt va 30 daqiqa vaqt. Hamma xizmatlar bepul.

> ⚠️ Kalitlarni (JSON fayl, API key, bot tokeni) va admin parolini hech kimga yubormang va GitHub’ga yuklamang. Ular faqat Render’ning **Environment** bo‘limida turadi.

---

## 0-qadam. Fayllarni ochish

1. Zip faylni yuklab oling.
2. **Fayllar (Files)** ilovasini oching → **Yuklanganlar (Downloads)** → zip faylni bosing. `taqdimot_sayt` papkasi paydo bo‘ladi.
3. Papka ichida 8 ta fayl bor: `server.js`, `package.json`, `index.html`, `app.js`, `admin.js`, `deck.js`, `export.js`, `README.md`.

## 1-qadam. GitHub — kodni joylash

1. Safari’da **github.com** ni oching → **Sign up** (akkaunt bo‘lsa — **Sign in**).
2. Yuqori o‘ngdagi **+** → **New repository**.
   - Repository name: `taqdimot`
   - **Public** ni tanlang (kodda maxfiy narsa yo‘q).
   - **Create repository**.
3. Ochilgan sahifada **uploading an existing file** havolasini bosing.
   - Ko‘rinmasa: manzil satridagi **aA** → **Kompyuter versiyasini so‘rash (Request Desktop Website)**.
4. **choose your files** → **Browse** → **Yuklanganlar** → `taqdimot_sayt` papkasi.
5. Yuqoridagi **Select (Tanlash)**ni bosib, 8 ta faylning hammasini belgilang → **Open**.
6. Pastda **Commit changes**ni bosing.

## 2-qadam. Render — serverni ishga tushirish

1. **render.com** → **Get Started** → **GitHub** bilan kiring.
2. **+ New** → **Web Service**.
3. Ro‘yxatdan `taqdimot` repozitoriyasini tanlang → **Connect**.
   - Ro‘yxat chiqmasa, **Public Git Repository** maydoniga `https://github.com/SIZNING_LOGININGIZ/taqdimot` ni yozing.
4. Sozlamalar:
   - **Name:** `taqdimot`
   - **Language:** Node
   - **Branch:** main
   - **Build Command:** `npm install`
   - **Start Command:** `node server.js`
   - **Instance Type:** **Free**
5. **Deploy Web Service**ni bosing va 2–5 daqiqa kuting.
6. Yuqorida server manzili chiqadi, masalan `https://taqdimot-abcd.onrender.com`. Uni Eslatmalar (Notes) ilovasiga saqlab qo‘ying.
7. Manzilni Safari’da ochsangiz, **«Saytni sozlash»** sahifasi chiqadi. Bu normal holat — kalitlar hali kiritilmagan.

## 3-qadam. Firebase — ma’lumotlar bazasi

Bu yerda foydalanuvchilar, limitlar va to‘lovlar saqlanadi.

1. **console.firebase.google.com** → Gmail bilan kiring → **Create a project** (yoki **Get started**).
   - Nomi: `taqdimot` → **Continue**.
   - Google Analytics’ni o‘chiring → **Create project**.
2. **Bazani yaratish:** chap menyu (☰) → **Build → Firestore Database** → **Create database**.
   - Edition so‘ralsa: **Standard**.
   - Location: istalgan, masalan `eur3`.
   - **Start in production mode** → **Create**.
3. **Maxfiy kalitni olish:** ⚙️ (Project Overview yonida) → **Project settings** → **Service accounts** → **Generate new private key** → **Generate key**.
   - `…firebase-adminsdk….json` fayli **Yuklanganlar**ga tushadi.

## 4-qadam. Gemini kaliti (sun’iy intellekt)

1. **aistudio.google.com** → Gmail bilan kiring → shartlarga rozilik bering.
2. **Get API key** → **Create API key**.
   - Loyiha so‘ralsa: `taqdimot` Firebase loyihasini tanlang.
3. `AIza…` bilan boshlanadigan kalitni nusxalab, Eslatmalar’ga saqlang.

## 5-qadam. Telegram bot

1. Telegram’da **@BotFather** ni oching → `/newbot`.
2. Bot nomini yozing, masalan `Taqdimot yasagich`.
3. Username yozing — oxiri `bot` bilan tugashi kerak, masalan `taqdimot_uz_bot`.
4. BotFather bergan **token**ni (`123456789:AA…` ko‘rinishida) nusxalab, Eslatmalar’ga saqlang.

Eski botingizdan foydalanmoqchi bo‘lsangiz: `/mybots` → bot → **API Token**. U bot boshqa joyda (masalan, eski Python kod bilan) ishlayotgan bo‘lsa, o‘shani to‘xtating — bitta token bilan ikki dastur ishlay olmaydi.

## 6-qadam. Kalitlarni Render’ga kiritish

1. Server manzilini Safari’da oching. **«Saytni sozlash»** sahifasida:
   - **Faylni tanlash (Choose File)** → **Yuklanganlar** → `…firebase-adminsdk….json`.
   - «✓ To‘g‘ri fayl» yozuvi chiqqach, **📋 Nusxa olish**ni bosing.
2. **dashboard.render.com** → `taqdimot` → chap menyuda **Environment** → **Add Environment Variable** (yoki **Edit**). Beshta qiymat qo‘shasiz:

| Key (nomi) | Value (qiymati) |
|---|---|
| `FIREBASE_SERVICE_ACCOUNT` | 1-bandda nusxalangan matn (joylang — Paste) |
| `GEMINI_API_KEY` | `AIza…` kalit |
| `TELEGRAM_BOT_TOKEN` | BotFather bergan token |
| `ADMIN_LOGIN` | admin panel uchun o‘zingiz o‘ylagan login, masalan `behruz` |
| `ADMIN_PASSWORD` | o‘zingiz o‘ylagan parol: kamida 8 belgi, harf va raqam aralash |

3. **Save, rebuild, and deploy** (yoki **Save Changes**) ni bosing va 2–3 daqiqa kuting.
4. Render → **Logs** da `Telegram bot ulandi: @…` yozuvi chiqishi kerak. Server botni o‘zi sozlaydi:
   - chat pastidagi menyu tugmasi — **Taqdimot**;
   - `/start` yozganlarga «📊 Taqdimot yasash» tugmali salom xabari;
   - `/admin` yozganlarga «⚙️ Admin panelni ochish» tugmasi.

## 7-qadam. Tekshirish va sozlash

1. Botingizga `/start` yozing → **📊 Taqdimot yasash** (yoki pastdagi **Taqdimot**) tugmasini bosing.
2. Mavzu yozib, taqdimot yasang → **Yuklab olish** → fayl bot chatiga keladi. Boshqa formatlarni ham sinab ko‘ring.
3. **Admin panel:** botga `/admin` yozing → **⚙️ Admin panelni ochish** → `ADMIN_LOGIN` va `ADMIN_PASSWORD` ni kiriting.
   - Telegram 7 kun eslab qoladi. Ilovaning yuqorisida ham **⚙️ Admin** tugmasi chiqadi — u faqat sizga ko‘rinadi.
   - Kerak bo‘lsa, admin panel Safari’da ham ochiladi: `https://taqdimot-abcd.onrender.com/admin`.
   - Admin panelni **bot ichida** ochganingizda Telegram akkauntingiz avtomatik ulanadi — foydalanuvchilar yuborgan cheklar shu akkauntga keladi. Ulanganlar ro‘yxati: **Sozlamalar → Cheklar keladigan Telegram akkauntlar**.
4. **Admin → Sozlamalar**:
   - karta raqami va karta egasi;
   - yordam uchun Telegram username;
   - paketlar: jeton soni, narxi (so‘m) va ⭐ Stars narxi (0 bo‘lsa, Stars bilan sotilmaydi);
   - do‘st taklif qilish bonusi (har nechta do‘stga nechta jeton);
   - majburiy kanal (ixtiyoriy): `@kanalingiz`. Botni kanalga **admin** qilib qo‘shing, aks holda tekshirib bo‘lmaydi.
5. **Saqlash**ni bosing.
6. Bot manzilini (`https://t.me/BOT_USERNAME`) do‘stlaringizga yuboring — hamma shu orqali foydalanadi.

---

## Admin sifatida kundalik ish

- **Admin panelni ochish:** botga `/admin` yozing (yoki ilovadagi **⚙️ Admin** tugmasi).
- **Chek kelganda:** bot sizga chek rasmini yuboradi. Kartangizga pul tushganini tekshiring → **✅ Tasdiqlash** (jetonlar avtomatik tushadi) yoki **❌ Rad etish**. Xuddi shuni **Admin → To‘lovlar**da ham qilsa bo‘ladi (**🧾 Chekni ko‘rish** tugmasi bor).
- **Stars to‘lovlari** o‘zi tushadi, sizga faqat xabar keladi. Stars’ni @BotFather → botingiz → **Balance** bo‘limida ko‘rasiz va yechib olasiz.
- **Hammaga xabar:** **Admin → 📣 Xabar** → matnni yozing → ikki marta bosing.
- **Qo‘lda qo‘shish yoki ayirish:** **Foydalanuvchilar** → `@username` yoki Telegram ID’ni yozing → **+1 / +5 / −1** yoki istalgan son.
- **Suiiste’mol qilgan odamni bloklash:** Foydalanuvchilar → **Bloklash**.
- **Statistika:** foydalanuvchilar soni, jami va bugun yasalgan taqdimotlar, daromad (karta) va tushgan ⭐ Stars.
- **Tarix:** kim qaysi mavzuda taqdimot yasagani.
- **Admin parolini o‘zgartirish:** Render → Environment → `ADMIN_PASSWORD` → yangi parol → **Save**. Barcha qurilmalardagi eski admin kirishlari o‘zi bekor bo‘ladi.

## Bilish kerak bo‘lgan cheklovlar

- **Render bepul tarifi:** 15 daqiqa hech kim foydalanmasa, server «uxlaydi». Buning oldini olish uchun server o‘zini har 10 daqiqada uyg‘otib turadi (`KEEP_AWAKE`), shuning uchun bot tez javob beradi. Bepul tarif oyiga 750 soat beradi — bitta server uchun yetadi. Render baribir qayta ishga tushirsa, birinchi ochilishga 1 daqiqagacha ketishi mumkin.
- **Stars qoidasi:** Telegram raqamli mahsulotlar uchun bot ichida Stars to‘lovini talab qiladi. Karta orqali to‘lov ham ishlaydi, lekin Stars’ni yoqib qo‘yish xavfsizroq.
- **Gemini bepul limiti:** kunlik va daqiqalik so‘rovlar soni cheklangan. Bir vaqtda ko‘p odam yasasa, «AI hozir band» xabari chiqadi. Bunda foydalanuvchining limiti qaytariladi. Ko‘proq kerak bo‘lsa, AI Studio’da to‘lovni (billing) yoqasiz — bu pullik.
- **Bir nechta akkaunt:** bitta odam bir nechta Telegram akkaunt bilan bepul taqdimotlarni qayta olishi mumkin. Shubhali akkauntlarni bloklaysiz yoki bepul sonini kamaytirasiz.
- **Hisob-kitob:** karta to‘lovini ilova o‘zi tekshirmaydi. Pul kelganini siz kartangizdan ko‘rib, ✅ ni bosasiz. Stars to‘lovini Telegram tekshiradi.
- **Namunadek usul:** eski `.ppt` formatini o‘qib bo‘lmaydi — PowerPoint’da .pptx qilib saqlash kerak. Namuna 20 MB gacha. Namunadagi grafiklar (diagramma-chart) ichidagi raqamlar o‘zgarmaydi. Logo, fon va bezak rasmlari o‘z joyida qoladi, faqat mazmunli fotolar almashadi.
- **Admin himoyasi:** bitta manzildan 5 marta noto‘g‘ri parol kiritilsa, 15 daqiqaga bloklanadi. Hammasi bo‘lib 50 ta xato urinish bo‘lsa, admin kirishi 15 daqiqaga hamma uchun yopiladi.
- **Brauzerda ochilsa:** server manzili Safari’da ochilsa, «Telegram’da ochish» tugmasi chiqadi. Ilovaning o‘zi faqat bot ichida ishlaydi (admin panel bundan mustasno).
- **Firebase:** bepul (Spark) reja bu ilova uchun yetarli.

## Muammo bo‘lsa

| Belgi | Yechim |
|---|---|
| «Saytni sozlash» sahifasi chiqyapti | Render → Environment’da nomlar aynan jadvaldagidek yozilganini tekshiring. Qiymatlar to‘liq nusxalangan bo‘lishi kerak. Render → **Logs** da `SOZLASH KERAK` qatoriga qarang. |
| Bot javob bermayapti | `TELEGRAM_BOT_TOKEN` to‘g‘ri nusxalanganini tekshiring. Render → Logs’da `Telegram bot ulandi` bormi? Eski bot dasturi shu token bilan ishlayotgan bo‘lsa, to‘xtating. Server uxlagan bo‘lsa, 1 daqiqa kuting. |
| «Bot sizga fayl yubora olmadi» | Foydalanuvchi avval botga `/start` yozishi kerak. |
| «Firestore bazasi yaratilmagan bo‘lishi mumkin» | 3-qadam, 2-band. |
| «Gemini kaliti noto‘g‘ri» | 4-qadam: yangi kalit yarating va `GEMINI_API_KEY` ni almashtiring. |
| «AI hozir band» | Bepul limit vaqtincha tugagan. Bir necha daqiqa kuting. |
| «Login yoki parol noto‘g‘ri» | Render → Environment’dagi `ADMIN_LOGIN` va `ADMIN_PASSWORD` ni tekshiring (katta-kichik harf ham farq qiladi). |
| «Juda ko‘p noto‘g‘ri urinish» | 15 daqiqa kuting, keyin qayta kiring. |
| «Admin login va paroli serverda kiritilmagan» | 6-qadam: `ADMIN_LOGIN` va `ADMIN_PASSWORD` ni qo‘shing. |
| «Sessiya eskirgan» | Ilova oynasini yopib, botdan qayta oching. |

## Kodni yangilash

GitHub → `taqdimot` → **Add file → Upload files** → yangi faylni yuklang (eski fayl o‘rniga yoziladi) → **Commit changes**. Render ilovani o‘zi qayta ishga tushiradi (2–5 daqiqa).

## Qo‘shimcha sozlamalar (ixtiyoriy, Render → Environment)

| Nomi | Nima uchun | Standart qiymat |
|---|---|---|
| `GEMINI_MODEL` | Qaysi Gemini modellari navbat bilan sinaladi (vergul bilan) | `gemini-3.8-flash,gemini-flash-latest,gemini-3.5-flash,gemini-4-hydrogen,gemini-3.5-flash-lite,gemini-flash-lite-latest` |
| `CROSSWORD_DAILY` | Bir foydalanuvchi kuniga nechta krossvord tuza oladi | `5` |
| `SITE_URL` | Server manzili (bot tugmalari uchun). Render o‘zi beradi — faqat o‘z domeningiz bo‘lsa yozasiz | Render manzili |
| `KEEP_AWAKE` | Server o‘zini har 10 daqiqada «uyg‘otib» turadi — bot birinchi bosishda ham tez javob beradi. `off` yozilsa, o‘chadi | `on` |
| `TELEGRAM_WEBHOOK` | `off` yozilsa, server bot xabarlariga javob bermaydi (faqat menyu tugmasini sozlaydi) | `on` |
| `FIREBASE_WEB_CONFIG` | Keyinchalik Telegram’dan tashqari oddiy sayt (Google bilan kirish) ham kerak bo‘lsa. Buning uchun Firebase’da Google kirishni ham yoqish kerak | yo‘q |

## Fayllar

| Fayl | Vazifasi |
|---|---|
| `server.js` | Server: Telegram kirishini tekshirish, limitlar, Gemini, faylni chatga yuborish, bot, to‘lov so‘rovlari, admin kirishi va API |
| `index.html` | Ilova sahifasi (dizayn va tugmalar) |
| `app.js` | Ilova mantig‘i: yasash, yuklab olish, to‘lov oynasi, admin kirish formasi |
| `admin.js` | Admin panel |
| `deck.js` | Slaydlar dizayni va PowerPoint yaratish |
| `export.js` | PDF, Word va rasmlar (ZIP) yaratish |
| `package.json` | Server uchun kerakli kutubxona ro‘yxati |

Ma’lumotlar Firestore’da saqlanadi:

- `users` — foydalanuvchilar va ularning limiti;
- `payments` — to‘lov so‘rovlari;
- `generations` va `decks` — yasalgan taqdimotlar;
- `settings` — karta, Telegram va paketlar;
- `stats` — statistika.
