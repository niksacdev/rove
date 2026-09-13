/* Strategy details live outside the scrollable composer. */
(function () {
  "use strict";
  let anchor = null, overlay = null, timer = null;
  function hide() {
    clearTimeout(timer);
    if (anchor) anchor.removeAttribute("aria-describedby");
    overlay?.remove(); overlay = null; anchor = null;
  }
  function delayHide() { clearTimeout(timer); timer = setTimeout(hide, 120); }
  function show(chip) {
    clearTimeout(timer);
    if (anchor === chip) return;
    hide();
    const source = chip.querySelector(".chip-popover");
    if (!source) return;
    anchor = chip;
    overlay = source.cloneNode(true);
    overlay.className = "strategy-tooltip";
    overlay.id = "strategy-details-tooltip";
    overlay.removeAttribute("aria-hidden");
    overlay.setAttribute("role", "tooltip");
    document.body.appendChild(overlay);
    chip.setAttribute("aria-describedby", overlay.id);
    const rect = chip.getBoundingClientRect(), tip = overlay.getBoundingClientRect();
    const margin = 12, gap = 8;
    const left = Math.max(margin, Math.min(rect.left + rect.width / 2 - tip.width / 2, window.innerWidth - tip.width - margin));
    const above = rect.top - tip.height - gap;
    const top = Math.max(margin, Math.min(above >= margin ? above : rect.bottom + gap, window.innerHeight - tip.height - margin));
    overlay.style.left = left + "px"; overlay.style.top = top + "px";
    overlay.addEventListener("pointerenter", () => clearTimeout(timer));
    overlay.addEventListener("pointerleave", delayHide);
  }
  document.addEventListener("pointerover", event => {
    const chip = event.target.closest?.(".strategy-chip");
    if (chip) show(chip);
  });
  document.addEventListener("pointerout", event => {
    if (anchor && anchor.contains(event.target) && !anchor.contains(event.relatedTarget)) delayHide();
  });
  document.addEventListener("focusin", event => {
    const chip = event.target.closest?.(".strategy-chip");
    if (chip) show(chip); else hide();
  });
  document.addEventListener("focusout", event => { if (anchor?.contains(event.target)) delayHide(); });
  document.addEventListener("keydown", event => { if (event.key === "Escape") hide(); });
  document.addEventListener("click", event => { if (!anchor?.contains(event.target) && !overlay?.contains(event.target)) hide(); });
  document.addEventListener("scroll", event => { if (!overlay?.contains(event.target)) hide(); }, true);
  window.addEventListener("resize", hide);
  window.addEventListener("popstate", hide);
  window.RoveTooltips = {hide};
})();
