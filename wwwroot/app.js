// Small helpers for the Blazor pages.
window.lectern = {
  // A new reader page: start at the top.
  pageShown() { window.scrollTo(0, 0); },
  // Keep the activity log scrolled to the bottom unless the reader scrolled up.
  followLog(id, first) {
    const el = document.getElementById(id);
    if (!el) return;
    if (first || el.scrollTop + el.clientHeight >= el.scrollHeight - 60) el.scrollTop = el.scrollHeight;
  },
};

// Copy buttons on code blocks in the reader.
document.addEventListener("click", (e) => {
  const b = e.target.closest && e.target.closest(".content .copy");
  if (!b) return;
  const code = b.parentNode.querySelector("code");
  if (!code || !navigator.clipboard) return;
  navigator.clipboard.writeText(code.innerText).then(() => {
    b.textContent = "Copied";
    setTimeout(() => (b.textContent = "Copy"), 1200);
  }, () => {});
});

// "/" focuses the search box.
document.addEventListener("keydown", (e) => {
  if (e.key !== "/" || /input|textarea/i.test(document.activeElement?.tagName || "")) return;
  const q = document.querySelector(".search");
  if (q) { e.preventDefault(); q.focus(); }
});
