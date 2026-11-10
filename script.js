// Yil
document.getElementById("year").textContent = new Date().getFullYear();

// Mavzu (yorug'/qorong'i)
const root = document.documentElement;
const themeBtn = document.querySelector(".theme-btn");
function setTheme(t) {
  root.dataset.theme = t;
  themeBtn.textContent = t === "dark" ? "☀️" : "🌙";
  try { localStorage.setItem("theme", t); } catch (e) {}
}
let saved = null;
try { saved = localStorage.getItem("theme"); } catch (e) {}
setTheme(saved || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
themeBtn.addEventListener("click", () => setTheme(root.dataset.theme === "dark" ? "light" : "dark"));

// Mobil menyu
const menuBtn = document.querySelector(".menu-btn");
const links = document.querySelector(".links");
menuBtn.addEventListener("click", () => {
  const open = links.classList.toggle("open");
  menuBtn.setAttribute("aria-expanded", open);
});
links.querySelectorAll("a").forEach(a => a.addEventListener("click", () => links.classList.remove("open")));

// Yozuv effekti
const words = ["dasturchiman", "dizaynerman", "o'quvchiman", "ijodkorman"];
const typed = document.getElementById("typed");
let wi = 0, ci = 0, deleting = false;
(function type() {
  const w = words[wi];
  typed.textContent = w.slice(0, ci);
  if (!deleting && ci < w.length) ci++;
  else if (deleting && ci > 0) ci--;
  else if (!deleting) { deleting = true; return setTimeout(type, 1400); }
  else { deleting = false; wi = (wi + 1) % words.length; }
  setTimeout(type, deleting ? 50 : 100);
})();

// Ko'rinishga kelganda animatsiya + raqamlar
function countUp(el) {
  const target = +el.dataset.count;
  const start = performance.now();
  (function step(now) {
    const p = Math.min((now - start) / 1200, 1);
    el.textContent = Math.round(target * p) + (p === 1 && target === 10 ? "+" : "");
    if (p < 1) requestAnimationFrame(step);
  })(start);
}
const io = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (!e.isIntersecting) return;
    e.target.classList.add("visible");
    e.target.querySelectorAll("[data-count]").forEach(countUp);
    io.unobserve(e.target);
  });
}, { threshold: 0.15 });
document.querySelectorAll(".reveal").forEach(el => io.observe(el));

// Aloqa formasi
const form = document.getElementById("contact-form");
const status = document.getElementById("form-status");
form.addEventListener("submit", e => {
  e.preventDefault();
  const data = new FormData(form);
  const ism = data.get("ism").trim();
  const email = data.get("email").trim();
  const xabar = data.get("xabar").trim();
  if (!ism || !xabar || !/^\S+@\S+\.\S+$/.test(email)) {
    status.className = "err";
    status.textContent = "Iltimos, barcha maydonlarni to'g'ri to'ldiring.";
    return;
  }
  status.className = "ok";
  status.textContent = `Rahmat, ${ism}! Xabaringiz qabul qilindi.`;
  form.reset();
});
