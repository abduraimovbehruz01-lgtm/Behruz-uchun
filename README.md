# Ish e’lonlari boti

Telegram bot: ish beruvchilar vakansiya joylaydi, ish qidiruvchilar ariza yuboradi.
Tashqi kutubxonalarsiz, Node.js 20+ da ishlaydi.

## Imkoniyatlar
- Vakansiya joylash (8 savol), oldindan ko‘rish, tahrirlash, AI bilan matnni yaxshilash (Gemini)
- Tariflar: oddiy / TOP (kanalda qadaladi), paketlar (kreditlar), muddatni uzaytirish
- To‘lov: karta + chek (admin tasdiqlaydi) va Telegram Stars (avtomatik)
- Ariza berish (bot orqali), arizalar ro‘yxati, «Ishchi topildi — yopish»
- Ish qidiruvchilar: qidiruv, obuna (soha/hudud), «Ish qidiryapman» e’loni, rezyume shablonlari
- Nomzodlar bazasi (pullik kirish)
- Kanal: navbat bilan va faqat kunduzi chiqarish, kechki «Bugungi vakansiyalar», avto o‘chirish
- Admin panel (/admin, login-parol): tekshiruv, cheklar, sozlamalar, statistika, hammaga xabar, bloklash, zaxira
- Majburiy obuna, shubhali so‘zlar filtri, telefon tasdiqlash

## Sozlamalar (Render → Environment)
| Nomi | Tavsif |
|---|---|
| `BOT_TOKEN` | @BotFather bergan token |
| `ADMIN_LOGIN`, `ADMIN_PASSWORD` | admin panelga kirish |
| `KANAL` | vakansiyalar kanali, masalan `@kanal_nomi` |
| `BAZA_KANAL` | ma’lumotlar zaxirasi uchun yopiq kanal ID raqami (`-100...`) |
| `GEMINI_API_KEY` | ixtiyoriy, AI yordamchi uchun |

Ishga tushirish: `node server.js`. Sinov: `npm test`.
