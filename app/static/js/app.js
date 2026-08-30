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
