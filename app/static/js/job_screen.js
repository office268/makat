/* מסך של עבודה שרצה במנות: תכנון, הפעלה, מעקב ועצירה.
 *
 * שני מסכים משתמשים בזה - גילוי מק"טים דרך המודל, וגריד Autodoc.
 * שניהם עושים בדיוק אותו דבר מול השרת: מבקשים תוכנית, פותחים הרצה,
 * ואז מבקשים מטרה אחת בכל פעם עד שהסטטוס מפסיק להיות "בתהליך".
 * ההבדל ביניהם הוא הכתובות והמילים, ולכן רק הם נכנסים כפרמטרים.
 *
 * מטרה אחת לבקשה, ולא הכל בבת אחת: קריאה למודל או גרידה של עמוד
 * לוקחת עשרות שניות, ו-gunicorn הורג בקשה אחרי 60.
 */
function jobScreen(options) {
  const form = document.getElementById(options.form);
  const urls = options.urls;
  const labels = options.labels;
  const bar = document.getElementById("progress-bar");
  const status = document.getElementById("job-status");
  const counters = document.getElementById("job-counters");
  const errorBox = document.getElementById("job-error");
  const logBox = document.getElementById("job-log");
  const sizeBox = document.getElementById("catalog-size");
  const runBtn = document.getElementById("btn-run");
  const stopBtn = document.getElementById("btn-stop");
  const token = document.querySelector('meta[name="csrf-token"]');
  let stopRequested = false;

  // ---- רשימת הדגמים לפי היצרן שנבחר ----
  const makeSelect = document.getElementById("make");
  const modelSelect = document.getElementById("model");

  makeSelect.addEventListener("change", async function () {
    const make = makeSelect.value;
    modelSelect.disabled = true;
    modelSelect.innerHTML = '<option value="">טוען…</option>';
    if (!make) {
      modelSelect.innerHTML = '<option value="">כל הדגמים — לפי הנפוצים</option>';
      refreshPlan();
      return;
    }
    try {
      const response = await fetch(
        "/api/vehicle-models?models_only=1&make=" + encodeURIComponent(make));
      const models = response.ok ? await response.json() : [];
      modelSelect.innerHTML = '<option value="">כל הדגמים — לפי הנפוצים</option>';
      models.forEach(function (name) {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        modelSelect.appendChild(option);
      });
      modelSelect.disabled = false;
    } catch (error) {
      modelSelect.innerHTML = '<option value="">שגיאה בטעינת הדגמים</option>';
    }
    refreshPlan();
  });

  modelSelect.addEventListener("change", refreshPlan);

  // ---- בחירת הכל, וספירת המטרות שיירוצו ----
  const selectAll = document.getElementById("select-all");
  const boxes = [...document.querySelectorAll('#part-types input[name="part_type"]')];
  const costNote = document.getElementById("cost-note");
  const planList = document.getElementById("plan-sample");
  let planTimer = null;

  // התוכנית מחושבת בשרת, כי שדה ריק מתמלא מקטלוג דגמי הרכב
  // ורק השרת יודע מי הדגמים הנפוצים
  async function refreshPlan() {
    const chosen = boxes.filter((box) => box.checked).length;
    selectAll.checked = chosen === boxes.length && chosen > 0;
    selectAll.indeterminate = chosen > 0 && chosen < boxes.length;

    clearTimeout(planTimer);
    planTimer = setTimeout(async function () {
      const params = new URLSearchParams(new FormData(form));
      params.delete("csrf_token");
      try {
        const response = await fetch(urls.plan + "?" + params);
        if (!response.ok) return;
        const plan = await response.json();
        costNote.textContent = plan.count
          ? `${plan.count} ${labels.unit} ${labels.perTarget}` +
            (plan.capped ? ` נחתך לתקרה של ${plan.max}.` : "") +
            (plan.source ? ` המטרות נבחרו ${plan.source}.` : "")
          : labels.empty;
        costNote.className = plan.count > 10
          ? "form-text mb-1 text-danger fw-bold" : "form-text mb-1";
        planList.innerHTML = "";
        plan.sample.forEach(function (line) {
          const item = document.createElement("li");
          item.textContent = line;
          planList.appendChild(item);
        });
        if (plan.count > plan.sample.length) {
          const more = document.createElement("li");
          more.textContent = `ועוד ${plan.count - plan.sample.length}…`;
          planList.appendChild(more);
        }
      } catch (error) { /* התצוגה המקדימה היא נוחות בלבד */ }
    }, 150);
  }

  selectAll.addEventListener("change", function () {
    boxes.forEach((box) => { box.checked = selectAll.checked; });
    refreshPlan();
  });
  boxes.forEach((box) => box.addEventListener("change", refreshPlan));
  refreshPlan();

  async function post(url, body) {
    const headers = {"X-Requested-With": "XMLHttpRequest"};
    if (token) headers["X-CSRFToken"] = token.content;
    const response = await fetch(url, {method: "POST", headers: headers, body: body});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "הבקשה נכשלה (" + response.status + ")");
    return data;
  }

  function render(data) {
    sizeBox.textContent = data.catalog_size;
    const job = data.job;
    if (!job) return;
    bar.style.width = job.progress_pct + "%";
    bar.textContent = job.progress_pct + "%";
    status.textContent = job.status_label;
    status.className = "badge " + (job.status === "done" ? "bg-success"
      : job.status === "running" ? "bg-primary" : "bg-secondary");
    counters.textContent = `${job.cursor}/${job.total} · נוספו ${job.created}` +
      ` · עודכנו ${job.updated} · נפסלו ${job.rejected}`;
    errorBox.classList.toggle("d-none", !job.error);
    if (job.error) errorBox.textContent = job.error;
    if (job.log && job.log.length) logBox.textContent = job.log.join("\n");
  }

  function busy(on) {
    runBtn.disabled = on;
    runBtn.textContent = on ? labels.busy : labels.run;
    stopBtn.classList.toggle("d-none", !on);
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    stopRequested = false;
    busy(true);
    errorBox.classList.add("d-none");
    try {
      render(await post(urls.start, new FormData(form)));
      let last = -1;
      while (!stopRequested) {
        const data = await post(urls.step);
        render(data);
        if (!data.job || !data.job.is_running) break;
        // רשת ביטחון: מטרה שלא התקדמה לא תיתקע בלולאה
        if (data.job.cursor === last) throw new Error(labels.stuck);
        last = data.job.cursor;
      }
    } catch (err) {
      errorBox.textContent = err.message;
      errorBox.classList.remove("d-none");
    } finally {
      busy(false);
    }
  });

  stopBtn.addEventListener("click", async function () {
    stopRequested = true;
    try { render(await post(urls.cancel)); } catch (e) { /* העצירה כבר בתוקף */ }
  });
}
