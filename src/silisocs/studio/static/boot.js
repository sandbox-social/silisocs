/* Critical-path bootstrap. Loaded blocking in <head> before anything else, so
 * only what genuinely has to run first lives here:
 *
 *  - the theme, applied before first paint (otherwise the page flashes the
 *    wrong palette on every navigation);
 *  - the palette-command queue, because page modules register commands while
 *    the document is still parsing — before the command dialog exists. The
 *    shell (studio.js) replaces this stub at the end of <body> and flushes it;
 *  - studioPageData(), the one way a page module reads its server-rendered
 *    data island.
 *
 * Everything else belongs in studio.js / panels.js / a page module. */

document.documentElement.dataset.theme =
  localStorage.getItem("silisocs-theme") ||
  (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");

window.paletteCommands = [];
window.registerPaletteCommand = command => window.paletteCommands.push(command);

/* One JSON island per page, written by the template as
 * `<script type="application/json" id="studio-page-data">{{ ...|tojson }}</script>`.
 * Jinja's `tojson` escapes `<`, `>`, `&` and `'` as \u00XX sequences, so run
 * data can never close the island or smuggle markup into it. */
window.studioPageData = () => {
  const island = document.getElementById("studio-page-data");
  return island ? JSON.parse(island.textContent) : {};
};
