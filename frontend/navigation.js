/* One navigation contract for every ROVE workspace. */
(function () {
  "use strict";
  const items = [
    ["evaluate", "Evaluate", "/static/datasets.html"],
    ["results", "Results", "/static/benchmarks.html"],
  ];
  function rootView(search) {
    const view = new URLSearchParams(search).get("view");
    return ["quick", "strategies", "models", "settings", "examples"].includes(view) ? view : "home";
  }
  function sectionForView(view) {
    return ["strategies", "models", "settings"].includes(view) ? "configure" : view === "home" ? "start" : "evaluate";
  }
  function setActive(section) {
    document.querySelectorAll("#roveNav [data-nav-section]").forEach(link => {
      if (link.dataset.navSection === section) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
  }
  function themeLabel() {
    const button = document.getElementById("themeToggle");
    if (button) { button.textContent = document.documentElement.dataset.theme === "dark" ? "Light mode" : "Dark mode"; button.setAttribute("aria-label", `Switch to ${button.textContent.toLowerCase()}`); }
  }
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme === "light" ? "light" : "dark";
    try { localStorage.setItem("rove-theme", document.documentElement.dataset.theme); } catch { /* Theme remains usable without storage. */ }
    themeLabel();
  }
  function mount() {
    const host = document.getElementById("roveNav");
    if (!host || host.dataset.mounted) return;
    host.dataset.mounted = "true"; host.className = "rove-nav";
    const brand = document.createElement("a"); brand.className = "rove-brand"; brand.href = "/"; brand.setAttribute("aria-label", "ROVE home"); brand.dataset.navSection = "start";
    brand.innerHTML = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="8" y="8" width="8" height="8" rx="2"/><path d="M12 2v6m0 8v6M2 12h6m8 0h6"/></svg><span>ROVE</span>';
    const nav = document.createElement("nav"); nav.setAttribute("aria-label", "Main navigation");
    for (const [key, label, href] of items) { const link = document.createElement("a"); link.href = href; link.textContent = label; link.dataset.navSection = key; nav.append(link); }
    const utilities = document.createElement("div"); utilities.className = "rove-utilities";
    const settings = document.createElement("a"); settings.href = "/?view=strategies"; settings.className = "rove-settings"; settings.dataset.navSection = "configure"; settings.setAttribute("aria-label", "Settings: strategies and connections");
    settings.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m9.2 3-.7 2.2-1.7 1L4.5 6l-2 3.5L4 11.2v1.7l-1.5 1.6 2 3.5 2.3-.2 1.7 1 .7 2.2h4l.7-2.2 1.7-1 2.3.2 2-3.5-1.5-1.6v-1.7l1.5-1.7L18 6l-2.3.2-1.7-1L13.3 3Z"/><circle cx="11.2" cy="12" r="3"/></svg><span>Settings</span>';
    const theme = document.createElement("button"); theme.type = "button"; theme.id = "themeToggle"; theme.className = "rove-theme";
    theme.addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
    utilities.append(settings, theme);
    host.replaceChildren(brand, nav, utilities);
    setActive(host.dataset.section || sectionForView(rootView(location.search))); themeLabel();
  }
  if (typeof module !== "undefined" && module.exports) module.exports = {items, rootView, sectionForView};
  if (typeof document !== "undefined") { window.RoveNavigation = {setActive, rootView, sectionForView, applyTheme}; mount(); }
})();
