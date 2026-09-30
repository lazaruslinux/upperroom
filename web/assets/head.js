// Runs in the <head> of every page, before anything is drawn: the channel's
// accent is remembered from the last visit, so the page paints in it at once
// instead of flashing the default and then correcting itself when the status
// poll arrives. Its own file because the pages allow no inline script, and
// wrapped so it leaves no names behind for the page's own scripts to trip on.
(function () {
  try {
    var saved = localStorage.getItem("selfstream_accent");
    if (saved) document.documentElement.dataset.accent = saved;
  } catch (e) {
    /* storage can be off; the default accent is a fine first paint */
  }
})();
