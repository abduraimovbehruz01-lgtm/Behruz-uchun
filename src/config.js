'use strict';
// Barcha sozlamalar Render → Environment bo‘limidan olinadi.
const env = process.env;
const clean = (v) => String(v || '').trim();

module.exports = {
  BOT_TOKEN: clean(env.BOT_TOKEN || env.TELEGRAM_BOT_TOKEN),
  ADMIN_LOGIN: clean(env.ADMIN_LOGIN),
  ADMIN_PASSWORD: clean(env.ADMIN_PASSWORD),
  // Vakansiyalar chiqadigan ochiq kanal: @kanal_nomi (admin paneldan ham o‘zgartirsa bo‘ladi)
  KANAL: clean(env.KANAL),
  // Ma’lumotlar zaxirasi saqlanadigan yopiq kanal ID raqami: -100...
  BAZA_KANAL: clean(env.BAZA_KANAL),
  GEMINI_API_KEY: clean(env.GEMINI_API_KEY),
  GEMINI_MODEL: clean(env.GEMINI_MODEL),
  GEMINI_BASE: clean(env.GEMINI_BASE) || 'https://generativelanguage.googleapis.com',
  TG_API: (clean(env.TG_API) || 'https://api.telegram.org').replace(/\/+$/, ''),
  PUBLIC_URL: clean(env.PUBLIC_URL || env.RENDER_EXTERNAL_URL).replace(/\/+$/, ''),
  PORT: Number(env.PORT || 10000),
  TEST: env.NODE_ENV === 'test',
};
