'use strict';
// Standart sozlamalar. Hammasini admin paneldan o‘zgartirsa bo‘ladi.
module.exports = () => ({
  channel: '', // vakansiyalar kanali: @kanal yoki -100...
  payments: false, // false = bepul rejim (boshlanishiga tavsiya etiladi)
  stars: true, // Telegram Stars bilan to‘lov
  card: '', // karta raqami
  cardOwner: '', // karta egasi
  support: '', // savollar uchun admin username
  // Narxlar: [so‘m, Stars]. 0 = bepul
  prices: {
    oddiy: [20000, 100],
    top: [50000, 250],
    ext: [15000, 75],
    res: [0, 0],
    cand: [50000, 250],
    tpl: [15000, 75],
  },
  // Kanalda turish muddati (soat). 48 soatgacha bo‘lsa, e’lon butunlay o‘chadi.
  hours: { oddiy: 48, top: 48, ext: 48, res: 48 },
  candDays: 7, // nomzodlar bazasiga kirish muddati (kun)
  resDbDays: 30, // rezyume bazada turadigan muddat (kun)
  remindHours: 3, // muddat tugashidan necha soat oldin eslatish
  topCredits: 2, // TOP e’lon necha kredit turadi
  packs: [
    { n: 5, uzs: 80000, xtr: 400 },
    { n: 10, uzs: 150000, xtr: 750 },
  ],
  cats: [
    'Savdo va sotuv',
    'Ofis va administratsiya',
    'IT va dasturlash',
    'Ta’lim',
    'Tibbiyot',
    'Qurilish va ta’mirlash',
    'Haydovchi va yetkazib berish',
    'Oshxona va restoran',
    'Go‘zallik va salomatlik',
    'Ishlab chiqarish',
    'Moliya va buxgalteriya',
    'Marketing va SMM',
    'Xizmat ko‘rsatish',
    'Boshqa',
  ],
  regions: [
    'Toshkent shahri',
    'Toshkent viloyati',
    'Andijon',
    'Buxoro',
    'Farg‘ona',
    'Jizzax',
    'Namangan',
    'Navoiy',
    'Qashqadaryo',
    'Qoraqalpog‘iston',
    'Samarqand',
    'Sirdaryo',
    'Surxondaryo',
    'Xorazm',
    'Masofaviy (onlayn)',
  ],
  queue: { interval: 10, from: 8, to: 22 }, // har 10 daqiqada, 08:00–22:00
  digest: { on: true, hour: 20 }, // kechki «Bugungi vakansiyalar»
  forceSub: true, // majburiy obuna (kanalga obuna bo‘lmaguncha bot ishlamaydi)
  phoneRequired: false, // vakansiya uchun telefon tasdiqlash majburiymi
  autoDelete: true, // muddati tugagan e’lonlarni o‘chirish
  cleanChat: true, // bot chatidagi eski xabarlarni o‘chirish
  resToChannel: true, // «Ish qidiryapman» e’loni kanalga chiqsinmi
  maxPerDay: 5, // bir kishi kuniga nechta vakansiya yubora oladi
  badWords: [
    'oldindan to\'lov', 'oldindan tolov', 'avans', 'viza', 'chet el', 'xorij', 'zagran',
    'karta raqam', 'investitsiya', 'kripto', 'bitkoin', 'bitcoin', 'tez pul', 'oson pul',
    'uyda o\'tirib', 'onlayn daromad', 'kuniga 1 mln', '18+', 'eskort', 'massaj',
    'предоплат', 'виза', 'за границ', 'инвестиц', 'крипт', 'легкий заработок', 'лёгкий заработок',
  ],
  tplFileId: '',
  tplName: '',
  tplType: 'document',
  tplDesc: 'Word formatidagi tayyor rezyume shablonlari: ochasiz, ma’lumotingizni yozasiz va ish beruvchiga yuborasiz.',
});
