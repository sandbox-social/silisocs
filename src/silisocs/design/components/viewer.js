// Shared runtime for the Silisocs backend platform viewers (microblog + forum).
//
// Loaded synchronously in <head> so the theme is applied before first paint and
// every helper below is defined before each page's inline bottom-of-body script
// runs. Each template keeps only its page-specific rendering and wires its own
// action handlers through `wireActions`.

// --- Theme bootstrap: applied before paint, kept in sync with the Studio host ---
(function () {
    const initial = new URLSearchParams(location.search).get('theme')
        || localStorage.getItem('silisocs-theme') || 'light';
    document.documentElement.dataset.theme = initial;
    addEventListener('message', (event) => {
        if (event.data?.type === 'silisocs-theme' && ['light', 'dark'].includes(event.data.theme)) {
            document.documentElement.dataset.theme = event.data.theme;
        }
    });
})();

// --- Deterministic avatar colour keyed on a string ---
const avatarColors = Array.from({ length: 8 }, (_, i) => `var(--categorical-${i})`);
function getColor(s) {
    let h = 0;
    const str = String(s ?? '');
    for (let i = 0; i < str.length; i++) h = str.charCodeAt(i) + ((h << 5) - h);
    return avatarColors[Math.abs(h) % avatarColors.length];
}

// --- Escape a value for safe insertion as element TEXT content ---
function esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

// --- Escape a value for safe insertion inside a double-quoted HTML ATTRIBUTE ---
// esc()/innerHTML leaves quotes unescaped, so a raw " would close the attribute
// and let following text parse as a new (executable) attribute. Any value that
// lands in an attribute — including data-* action payloads — must use this.
function escAttr(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

function timeSince(ts) {
    const s = Math.floor((Date.now() / 1000) - ts);
    if (s < 0) return 'now';
    if (s < 60) return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm';
    if (s < 86400) return Math.floor(s / 3600) + 'h';
    if (s < 2592000) return Math.floor(s / 86400) + 'd';
    return Math.floor(s / 2592000) + 'mo';
}

// --- Shared line icons -----------------------------------------------------
// Templates name a semantic icon instead of carrying backend-local SVG or
// Unicode glyphs. Custom viewers can use the same helper without copying paths.
const viewerIcons = Object.freeze({
    admin: '<rect width="7" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="14" y="3" rx="1"/><rect width="7" height="7" x="3" y="14" rx="1"/><rect width="7" height="7" x="14" y="14" rx="1"/>',
    arrowLeft: '<path d="m15 18-6-6 6-6"/>',
    comment: '<path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/>',
    explore: '<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2.1 4.9-4.9 2.1 2.1-4.9z"/>',
    heart: '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8l1.1 1.1L12 21.2l7.8-7.8a5.5 5.5 0 0 0 1-8.8z"/>',
    home: '<path d="m3 11 9-8 9 8"/><path d="M5 10v10h14V10"/><path d="M9 20v-6h6v6"/>',
    reply: '<path d="m9 17-5-5 5-5"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/>',
    repost: '<path d="m17 2 4 4-4 4"/><path d="M3 10V8a2 2 0 0 1 2-2h16"/><path d="m7 22-4-4 4-4"/><path d="M21 14v2a2 2 0 0 1-2 2H3"/>',
    share: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.6 10.5 6.8-4M8.6 13.5l6.8 4"/>',
    trending: '<path d="m3 17 6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
    users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
    voteDown: '<path d="m18 9-6 6-6-6"/>',
    voteUp: '<path d="m6 15 6-6 6 6"/>',
});

function viewerIcon(name, className = '') {
    const paths = viewerIcons[name];
    if (!paths) return '';
    return `<svg class="viewer-icon ${escAttr(className)}" viewBox="0 0 24 24" fill="none" `
        + `stroke="currentColor" stroke-width="1.8" stroke-linecap="round" `
        + `stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
}

function hydrateViewerIcons(root = document) {
    root.querySelectorAll('[data-viewer-icon]').forEach((element) => {
        element.innerHTML = viewerIcon(element.dataset.viewerIcon);
    });
}

document.addEventListener('DOMContentLoaded', () => hydrateViewerIcons());

// --- Click delegation ---
// Elements carry `data-action` plus a data-* payload instead of an inline
// onclick string, so user-derived values never land in an executable attribute.
// One delegated listener dispatches to the handler named by the nearest
// [data-action] ancestor, so nested actionable elements resolve to the innermost
// one without needing event.stopPropagation().
function wireActions(handlers) {
    document.addEventListener('click', (event) => {
        const el = event.target.closest('[data-action]');
        if (!el) return;
        const handler = handlers[el.dataset.action];
        if (handler) handler(el.dataset, el, event);
    });
}
