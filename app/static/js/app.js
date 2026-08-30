// חיווי המתנה בשליחת טופס. זיהוי הרכב פונה למאגר משרד התחבורה, וזו
// יכולה להיות שנייה או שתיים שבהן המסך נראה תקוע - במיוחד בטלפון,
// שבו אין ללחיצה שום משוב עד שהדף הבא נטען.
(function () {
  // event.submitter לא קיים בדפדפנים ישנים; שומרים את הכפתור האחרון שנלחץ
  let lastClicked = null;
  document.addEventListener("click", function (event) {
    const button = event.target.closest("button[type=submit], button:not([type])");
    if (button) lastClicked = button;
  });

  document.addEventListener("submit", function (event) {
    const form = event.target.closest("[data-busy-form]");
    if (!form) return;

    const submitter = event.submitter || lastClicked;
    const buttons = form.querySelectorAll("button[type=submit], button:not([type])");

    // שם וערך של הכפתור שנלחץ נשמרים גם בשדה מוסתר: יש דפדפנים
    // שמשמיטים אותם כשהכפתור עצמו מושבת תוך כדי השליחה, וה-action
    // הוא מה שקובע איזה משני השלבים ירוץ
    if (submitter && submitter.name && form.contains(submitter)) {
      const carry = document.createElement("input");
      carry.type = "hidden";
      carry.name = submitter.name;
      carry.value = submitter.value;
      form.appendChild(carry);
    }

    buttons.forEach(function (button) {
      if (!button.dataset.idleHtml) button.dataset.idleHtml = button.innerHTML;
      if (button === submitter && button.dataset.busyLabel) {
        button.innerHTML =
          '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>' +
          button.dataset.busyLabel;
      }
      button.disabled = true;
    });
  });

  // חזרה עם "אחורי" מגישה את הדף מהמטמון בדיוק כפי שנעזב - כלומר
  // עם הכפתורים מושבתים ומסתובבים. משחזרים אותם למצב מנוחה.
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    document.querySelectorAll("[data-busy-form] button[disabled]").forEach(
      function (button) {
        if (button.dataset.idleHtml) button.innerHTML = button.dataset.idleHtml;
        button.disabled = false;
      }
    );
  });
})();

// חיווי המתנה בלחיצה על קישור. תגית כיסוי פותחת ניווט מלא שמריץ
// שאילתה בשרת, ובלעדיו המסך נראה תקוע בדיוק כמו בשליחת טופס.
(function () {
  document.addEventListener("click", function (event) {
    const link = event.target.closest("[data-busy-link]");
    if (!link || event.metaKey || event.ctrlKey || event.shiftKey) return;

    const strip = link.parentElement;
    strip.querySelectorAll("[data-busy-link]").forEach(function (other) {
      if (other !== link) other.classList.add("opacity-50");
      other.style.pointerEvents = "none";
    });
    if (!link.dataset.idleHtml) link.dataset.idleHtml = link.innerHTML;
    link.innerHTML =
      '<span class="spinner-border spinner-border-sm ms-1" role="status" aria-hidden="true"' +
      ' style="width: .7rem; height: .7rem"></span>' + link.dataset.idleHtml;
  });

  // חזרה עם "אחורי" מגישה את הדף מהמטמון כפי שנעזב - עם הספינר בפנים
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    document.querySelectorAll("[data-busy-link]").forEach(function (link) {
      if (link.dataset.idleHtml) link.innerHTML = link.dataset.idleHtml;
      link.classList.remove("opacity-50");
      link.style.pointerEvents = "";
    });
  });
})();

// מספר רישוי עם מקפים במקום הנכון, תוך כדי הקלדה.
// מעצבים רק כשהסמן בסוף, כדי לא להקפיץ אותו בעריכה באמצע.
(function () {
  function formatted(digits) {
    if (digits.length === 8) {
      return digits.slice(0, 3) + "-" + digits.slice(3, 5) + "-" + digits.slice(5);
    }
    if (digits.length === 7) {
      return digits.slice(0, 2) + "-" + digits.slice(2, 5) + "-" + digits.slice(5);
    }
    return digits;
  }

  function reformat(field, onlyAtEnd) {
    const atEnd = field.selectionStart === field.value.length;
    if (onlyAtEnd && !atEnd) return;
    const digits = field.value.replace(/\D/g, "").slice(0, 8);
    const next = formatted(digits);
    if (next === field.value) return;
    field.value = next;
    if (atEnd) field.setSelectionRange(next.length, next.length);
  }

  document.addEventListener("input", function (event) {
    if (event.target.matches(".plate-input")) reformat(event.target, true);
  });
  document.addEventListener("blur", function (event) {
    if (event.target.matches(".plate-input")) reformat(event.target, false);
  }, true);
})();

// שורות חוזרות בטופס המק"ט - התאמות לרכב ומק"טים מקבילים
document.addEventListener("click", function (event) {
  const addButton = event.target.closest("[data-add-row]");
  if (addButton) {
    const container = document.getElementById(addButton.dataset.addRow);
    const template = container.querySelector(".repeat-row");
    const clone = template.cloneNode(true);
    clone.querySelectorAll("input").forEach((field) => (field.value = ""));
    clone.querySelectorAll("select").forEach((field) => (field.selectedIndex = 0));
    container.appendChild(clone);
    clone.querySelector("input").focus();
    return;
  }

  const removeButton = event.target.closest("[data-remove-row]");
  if (removeButton) {
    const row = removeButton.closest(".repeat-row");
    const container = row.parentElement;
    // תמיד משאירים שורה אחת ריקה, אחרת אי אפשר להוסיף מחדש
    if (container.querySelectorAll(".repeat-row").length > 1) {
      row.remove();
    } else {
      row.querySelectorAll("input").forEach((field) => (field.value = ""));
    }
  }
});

// כפתור ההתקנה. הדפדפן מודיע כשהאפליקציה ניתנת להתקנה, ורק אז
// מציגים אותו - כפתור שלא עושה כלום גרוע מכפתור שלא קיים.
(function () {
  let installEvent = null;
  const button = document.getElementById("install-app");
  if (!button) return;

  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    installEvent = event;
    button.classList.remove("d-none");
  });

  button.addEventListener("click", async function () {
    if (!installEvent) return;
    installEvent.prompt();
    const choice = await installEvent.userChoice;
    if (choice.outcome === "accepted") button.classList.add("d-none");
    installEvent = null;
  });

  window.addEventListener("appinstalled", function () {
    button.classList.add("d-none");
  });
})();

// השלמת דגמים לפי היצרן שנבחר, מקטלוג משרד התחבורה
document.addEventListener("input", async function (event) {
  if (!event.target.matches('input[name="fit_make"]')) return;
  const make = event.target.value.trim();
  if (make.length < 2) return;
  try {
    const response = await fetch(
      `/api/vehicle-models?models_only=1&make=${encodeURIComponent(make)}`
    );
    if (!response.ok) return;
    const models = await response.json();
    const list = document.getElementById("models");
    if (!list) return;
    list.innerHTML = "";
    models.forEach(function (model) {
      const option = document.createElement("option");
      option.value = model;
      list.appendChild(option);
    });
  } catch (error) {
    // השלמה היא נוחות בלבד - כשל ברשת לא אמור להפריע להקלדה
  }
});

// חזרה לאפליקציה אחרי הפסקה.
//
// הדפדפן משחזר דף מהזיכרון בלי לפנות לשרת - PWA שנפתח מחדש, לשונית
// שחוזרים אליה, כפתור "אחורי". במצבים האלה לא מגיעה לשרת שום בקשה,
// והשער שמפנה למסך הפתיחה לא מקבל בכלל הזדמנות לרוץ. לכן הדף מחזיר
// את עצמו: הוא מודד כמה זמן היה מוסתר, ומגיע לאותה מסקנה כמו השרת.
//
// רק בדלת הכניסה, ורק כשאין עליה תוצאות: מי שהשאיר את האפליקציה על
// מסך פנימי או על תוצאות חיפוש חוזר למה שהשאיר, בדיוק כמו שקישור
// עמוק לא נחסם.
(function () {
  const idleSeconds = Number(document.body.dataset.splashIdle || 0);
  const splashUrl = document.body.dataset.splashUrl;
  if (!idleSeconds || !splashUrl) return;
  if (window.location.pathname !== "/" || window.location.search) return;

  let hiddenAt = null;
  let leaving = false;

  function backFromABreak() {
    return hiddenAt !== null && Date.now() - hiddenAt > idleSeconds * 1000;
  }

  function toSplash() {
    if (leaving) return;
    leaving = true;
    // replace ולא href: החזרה למסך הפתיחה אינה תחנה בהיסטוריה
    window.location.replace(splashUrl);
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      hiddenAt = Date.now();
    } else if (backFromABreak()) {
      toSplash();
    }
  });

  // דף ששוחזר מהמטמון לא הריץ אף בקשה, אבל שמר את המשתנים שלמעלה
  window.addEventListener("pageshow", function (event) {
    if (event.persisted && backFromABreak()) toSplash();
  });
})();
