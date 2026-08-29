// כפתורי מספרי הרישוי לדוגמה במסך הדמו
document.addEventListener("click", function (event) {
  const button = event.target.closest(".plate-pick");
  if (!button) return;
  const input = document.querySelector(".plate-input");
  if (input) {
    input.value = button.dataset.plate;
    input.focus();
  }
});
