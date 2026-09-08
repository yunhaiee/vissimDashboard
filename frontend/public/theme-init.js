// Prism theme stamp. Runs BEFORE first paint (next/script beforeInteractive,
// wired in app/layout.tsx) so a reader who chose the light theme never sees a
// dark frame on the way there. Only light is ever written: the default theme
// is the absence of the attribute, so a fresh browser renders exactly the HTML
// the server sent and hydration has nothing to reconcile. The storage key is
// the one lib/theme.ts exports; the contract suite pins the two together.
(function () {
  try {
    if (window.localStorage.getItem("vissim-theme") === "light") {
      document.documentElement.setAttribute("data-theme", "light");
    }
  } catch (_error) {
    // Storage can be disabled (private/embedded contexts): stay on the default.
  }
})();
