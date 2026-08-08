/* The half of the composer that the scenario editor and the study editor are
 * the same program in.
 *
 * Both pages are "edit a YAML document through a form, mirror it back, and
 * save it optimistically": they had byte-for-byte copies of the dirty flag,
 * the unload guard, the compose round trip and the 409-conflict save flow.
 * That shared shape lives here, once, and is loaded before scenario.js /
 * study.js. What genuinely differs — a multi-file mirror with edit history vs
 * one document, each page's own field<->value projection, launch vs evaluator
 * wiring — stays in the page module. Nothing is parameterised here that only
 * one of the two callers uses.
 *
 * Depends on: studio.js (apiFetch, apiError, notify, withBusy, saveConflict,
 * showSaveConflict), which the shell loads blocking in <head> first. */

/* One editor's unsaved-changes state: the save-state chip it writes, and the
 * guard that stops a navigation away from edits nobody has written yet. */
window.composerDirtyState = stateId => {
  let dirty = false;
  addEventListener("beforeunload", event => {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
  const write = (text, isDirty) => {
    dirty = isDirty;
    const chip = document.getElementById(stateId);
    if (chip) {
      chip.textContent = text;
      chip.classList.toggle("dirty", isDirty);
    }
  };
  return {
    markDirty: () => write("Unsaved changes", true),
    markSaved: () => write("Saved", false),
    // Taking the other editor's version is a deliberate discard, so the guard
    // must not fire on the way out of the reload it triggers.
    discard: () => {
      dirty = false;
    },
  };
};

/* The action buttons a composer holds for the length of a round trip
 * (withBusy, studio.js). Both pages address them by their test id. */
window.composerButton = name => document.querySelector(`[data-testid="${name}"]`);

/* A JSON POST whose failure is already reported: the parsed body, or null when
 * the request failed (apiFetch raised the danger toast) so the caller stops
 * instead of proceeding on a body it never received. */
window.composerPost = async (url, body) => {
  const response = await apiFetch(url, {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify(body),
  });
  return response ? await response.json() : null;
};

/* The optimistic-concurrency save, whole.
 *
 * The editor sends the fingerprint (and the text) it was opened on, so a
 * document another tab already changed comes back 409 and becomes the conflict
 * dialog instead of an overwrite. This stays on raw `fetch`: a 409 is a dialog,
 * not a toast.
 *
 *   button        the control to hold for the round trip
 *   url, body()   where the save goes and what it carries (read at call time,
 *                 so an Overwrite retry sends the adopted fingerprint)
 *   noun          "scenario" / "study", for the discard confirmation
 *   dirty         this editor's composerDirtyState
 *   adoptConflict take the other editor's fingerprint before retrying — the one
 *                 thing a multi-file editor does differently from a single one
 *   adopt(saved)  record the fingerprint the server wrote back
 *   retry()       the caller's own save, re-entered after Overwrite
 *
 * Resolves true when the document was written, false otherwise (including
 * "keep editing"), so a caller mid-action — Launch — can stop. */
window.composerSave = ({button, url, body, noun, dirty, adoptConflict, adopt, retry}) =>
  withBusy(button, async () => {
    const response = await fetch(url, {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify(body()),
    });
    const conflict = await saveConflict(response);
    if (conflict)
      return await showSaveConflict(conflict, {
        onReload: () => {
          if (!confirm(`Reloading ${conflict.file} discards your unsaved edits to this ${noun}.`))
            return;
          dirty.discard();
          location.reload();
        },
        onOverwrite: async () => {
          adoptConflict(conflict);
          return await retry();
        },
      });
    if (response.ok) {
      adopt(await response.json());
      dirty.markSaved();
    }
    notify(response.ok ? "Saved" : await apiError(response), response.ok ? "success" : "danger");
    return response.ok;
  });
