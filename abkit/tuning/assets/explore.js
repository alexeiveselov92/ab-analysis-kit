// Placeholder explore bundle (M3 WP6). The real cockpit client is built from
// web/src/explore/ by web/build.mjs in WP7 and committed here (the same
// 2-file packaging contract as abkit/reporting/assets/report.js). This stub
// keeps the page self-contained and honest until then.
window.__ABK_EXPLORE__ = {
  render: function (payload, mount) {
    var name = payload && payload.experiment ? String(payload.experiment) : "experiment";
    var note = document.createElement("div");
    note.className = "abk-explore";
    note.style.cssText = "max-width:720px;margin:15vh auto;padding:24px;font:15px/1.5 system-ui;";
    var title = document.createElement("h1");
    title.style.cssText = "font-size:20px;margin:0 0 8px;";
    title.textContent = "abkit explore — " + name;
    var body = document.createElement("p");
    body.textContent =
      "The explore cockpit client lands in M3 WP7 (web/src/explore/). " +
      "The server, payload, recompute engine and Apply seam behind this page are live.";
    note.appendChild(title);
    note.appendChild(body);
    mount.appendChild(note);
  },
};
