// Event delegation throughout: htmx (hx-boost) swaps #page on every action,
// so handlers live on document and re-run initPage after each swap.

function openDialog(id) {
  const dlg = document.getElementById(id);
  if (dlg && !dlg.open) {
    dlg.showModal();
    const focus = dlg.querySelector("[autofocus], #pick-q");
    if (focus) focus.focus();
  }
}

// Dialogs driven by the URL (?dlg=...) close by following their ✕ link so the
// server state matches; plain dialogs just close.
function closeDialog(dlg) {
  const link = dlg.querySelector("a.dlg-close");
  if (link) link.click(); else dlg.close();
}

function setPref(field, value, btn) {
  const attr = field === "accent" ? "data-accent" : "data-theme";
  const defaultValue = field === "accent" ? "teal" : "system";
  if (value === defaultValue) document.documentElement.removeAttribute(attr);
  else document.documentElement.setAttribute(attr, value);
  btn.closest(".accent-row").querySelectorAll("[data-" + field + "-value]")
    .forEach((x) => x.classList.toggle("active", x === btn));
  const body = new URLSearchParams();
  body.set(field, value);
  fetch("/prefs", { method: "POST", body });
}

function parseNum(text) {
  const v = parseFloat(String(text || "0").replace(",", "."));
  return Number.isFinite(v) ? v : 0;
}
function fmtNum(v) {
  return (Math.round(v * 10) / 10).toString().replace(".", ",");
}

let longPressed = false;
let pressTimer = null;

document.addEventListener("click", (e) => {
  const opener = e.target.closest("[data-open]");
  if (opener) {
    const host = opener.closest("dialog");
    if (host && opener.hasAttribute("data-close")) host.close();
    return openDialog(opener.dataset.open);
  }
  const closer = e.target.closest("[data-close]");
  if (closer) return closer.closest("dialog").close();
  if (e.target instanceof HTMLDialogElement) return closeDialog(e.target); // backdrop

  const toggler = e.target.closest("[data-toggle]");
  if (toggler) {
    const target = document.querySelector(toggler.dataset.toggle);
    if (target) {
      target.classList.toggle("hidden");
      const input = target.querySelector("input:not([type=hidden])");
      if (input && !target.classList.contains("hidden")) input.focus();
    }
    if (toggler.tagName === "A") e.preventDefault();
    return;
  }

  const step = e.target.closest("[data-step]");
  if (step) {
    const wrap = step.closest("[data-stepper]");
    const input = wrap.querySelector("input");
    const min = parseNum(input.dataset.min ?? "0");
    const next = Math.max(min, parseNum(input.value) + parseNum(step.dataset.step));
    input.value = fmtNum(next);
    if (wrap.hasAttribute("data-submit")) wrap.closest("form").requestSubmit();
    return;
  }

  const accentBtn = e.target.closest("[data-accent-value]");
  if (accentBtn) return setPref("accent", accentBtn.dataset.accentValue, accentBtn);
  const themeBtn = e.target.closest("[data-theme-value]");
  if (themeBtn) return setPref("theme", themeBtn.dataset.themeValue, themeBtn);

  // Shopping row: tap anywhere = the round button; long press = «есть дома».
  const row = e.target.closest("[data-longpress]");
  if (row) {
    if (longPressed) { longPressed = false; e.preventDefault(); return; }
    if (!e.target.closest("button, input, a")) {
      e.preventDefault();
      row.querySelector(".cb").click();
    }
  }
});

document.addEventListener("pointerdown", (e) => {
  const row = e.target.closest("[data-longpress]");
  if (!row || e.target.closest("button")) return;
  clearTimeout(pressTimer);
  pressTimer = setTimeout(() => {
    longPressed = true;
    const have = row.querySelector(".have-btn");
    if (have) have.click();
  }, 550);
});
["pointerup", "pointercancel", "pointermove"].forEach((evt) =>
  document.addEventListener(evt, (e) => {
    if (evt === "pointermove" && e.pressure > 0 && Math.abs(e.movementX) + Math.abs(e.movementY) < 4) return;
    clearTimeout(pressTimer);
  }));

// Chip-style checkboxes and segmented radios reflect their state visually.
document.addEventListener("change", (e) => {
  const chip = e.target.closest(".tagchip.filter");
  if (chip && e.target.type === "checkbox") chip.classList.toggle("on", e.target.checked);
  const seg = e.target.closest(".seg");
  if (seg && e.target.type === "radio") {
    seg.querySelectorAll("label").forEach((l) => l.classList.toggle("on", l.contains(e.target)));
  }
});

// Esc on a URL-driven dialog: follow the close link instead of just hiding it.
document.addEventListener("cancel", (e) => {
  if (e.target instanceof HTMLDialogElement && e.target.querySelector("a.dlg-close")) {
    e.preventDefault();
    closeDialog(e.target);
  }
}, true);

function openFromState() {
  const page = document.getElementById("page");
  if (page && page.dataset.dlg) openDialog("dlg-" + page.dataset.dlg);
}
function initPage() {
  openFromState();
}
document.addEventListener("DOMContentLoaded", initPage);
document.body.addEventListener("htmx:afterSwap", (e) => { if (e.target.id === "page") initPage(); });
