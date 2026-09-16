function applyTheme(theme) {
  const resolved = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = resolved;
  localStorage.setItem("rag-theme", resolved);
  const toggle = document.querySelector("#theme-toggle");
  if (!toggle) return;
  const dark = resolved === "dark";
  toggle.setAttribute("aria-pressed", String(dark));
  toggle.innerHTML = `<i class="ph ${dark ? "ph-sun" : "ph-moon"}" aria-hidden="true"></i><span>${dark ? "浅色" : "深色"}</span>`;
}

document.addEventListener("DOMContentLoaded", () => {
  applyTheme(document.documentElement.dataset.theme);
  document.querySelector("#theme-toggle")?.addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });
  document.querySelectorAll("[data-view-target]").forEach(button => {
    button.addEventListener("click", () => switchView(button.dataset.viewTarget));
  });
});
