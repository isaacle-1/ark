/* Apply the saved theme before first paint — avoids a flash of the wrong look.
 * Loaded as a classic (non-module) script from index.html <head>, served by
 * the app itself (CSP script-src 'self'). Inline version would violate the
 * server's CSP, so it lives here as a real file. */
(function () {
  try {
    var saved = window.localStorage.getItem("ark-theme");
    if (saved === "dark" || saved === "light" || saved === "rednight") {
      document.documentElement.setAttribute("data-theme", saved);
    } else {
      document.documentElement.setAttribute("data-theme", "dark");
    }
  } catch (e) {
    document.documentElement.setAttribute("data-theme", "dark");
  }
})();