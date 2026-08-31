// גודל התצוגה: זום וגודל טקסט.
//
// שני דברים שונים בכוונה. הזום מגדיל את הדף כולו - טקסט, כפתורים,
// מסגרות ותמונות - בדיוק כמו הזום של הדפדפן, והפריסה נשארת כשהייתה.
// גודל הטקסט משנה את גודל הבסיס שכל הטיפוגרפיה נמדדת ממנו: הטקסט
// והמרווחים שסביבו גדלים, התמונות לא, והשורות נשברות מחדש. במוסך
// שבו מסתכלים על טבלת מק"טים ממרחק זרוע צריך את שניהם.
//
// הקובץ נטען ב-<head> ולא בסוף הדף: הוא מחיל את מה שנשמר לפני הציור
// הראשון, אחרת הדף היה נפתח בגודל רגיל וקופץ לגודלו רגע אחר כך.
(function () {
  "use strict";

  const KEY = "makat.display";
  const DEFAULTS = { zoom: 1, font: 1 };

  // גבולות שנבחרו כדי שהמסך יישאר שמיש: קטן מדי הופך את הטבלאות
  // לבלתי קריאות, וגדול מדי מוציא את הניווט מהמסך.
  const LIMITS = {
    zoom: { min: 0.7, max: 1.8, step: 0.1 },
    font: { min: 0.8, max: 1.8, step: 0.1 },
  };

  function clamp(name, value) {
    const limits = LIMITS[name];
    const number = Number(value);
    if (!isFinite(number) || !number) return DEFAULTS[name];
    // עיגול לשתי ספרות: חיבור חוזר של 0.1 צובר שארית בינארית
    return Math.round(Math.min(limits.max, Math.max(limits.min, number)) * 100) / 100;
  }

  function read() {
    try {
      const saved = JSON.parse(window.localStorage.getItem(KEY) || "{}");
      return { zoom: clamp("zoom", saved.zoom), font: clamp("font", saved.font) };
    } catch (error) {
      // גלישה פרטית או אחסון חסום - ההגדרה פשוט לא נזכרת
      return Object.assign({}, DEFAULTS);
    }
  }

  function write() {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(state));
    } catch (error) {
      // אותו דבר בכיוון השני: אי אפשר לשמור, אבל המסך כן משתנה
    }
  }

  function apply() {
    const root = document.documentElement;
    root.style.setProperty("--app-zoom", state.zoom);
    root.style.setProperty("--app-font-scale", state.font);
  }

  let state = read();
  apply();

  function refresh() {
    document.querySelectorAll("[data-display-value]").forEach(function (readout) {
      readout.textContent = Math.round(state[readout.dataset.displayValue] * 100) + "%";
    });
    // כפתור שהגיע לקצה מושבת, כדי שהלחיצה הבאה תספר שאין לאן להמשיך
    document.querySelectorAll("[data-display-step]").forEach(function (button) {
      const [name, direction] = button.dataset.displayStep.split(":");
      button.disabled = clamp(name, state[name] + Number(direction) * LIMITS[name].step)
        === state[name];
    });
  }

  function step(name, direction) {
    const next = clamp(name, state[name] + direction * LIMITS[name].step);
    if (next === state[name]) return;
    state[name] = next;
    apply();
    write();
    refresh();
  }

  document.addEventListener("click", function (event) {
    const stepper = event.target.closest("[data-display-step]");
    if (stepper) {
      const [name, direction] = stepper.dataset.displayStep.split(":");
      step(name, Number(direction));
      return;
    }
    if (event.target.closest("[data-display-reset]")) {
      state = Object.assign({}, DEFAULTS);
      apply();
      write();
      refresh();
    }
  });

  document.addEventListener("DOMContentLoaded", refresh);
})();
