// Пересчёт медианы дневных графиков по выбранному диапазону (кнопки, ползунок, зум).
// Ряды передаются из Python явными массивами: числа внутри Plotly упакованы в base64.
(function () {
  var CFG = __CFG__;
  var DYN = "__DYN__";

  function median(a) {
    if (!a.length) return null;
    var s = a.slice().sort(function (x, y) { return x - y; }), n = s.length;
    return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
  }

  function label(base, m, n, suffix) {
    return base + "&nbsp;&nbsp;&nbsp;&nbsp;<span style=\"color:" + DYN + "\">"
      + "—·— Медиана за выбранный период: " + m.toFixed(2) + suffix
      + " (" + n + " дн.)</span>";
  }

  function attach(cfg) {
    var gd = document.getElementById(cfg.id);
    if (!gd || !window.Plotly || !gd.layout) return;
    var busy = false;

    function update() {
      if (busy) return;
      var ax = gd.layout.xaxis;
      var r = (ax && !ax.autorange && ax.range) ? ax.range : null;
      var lo = r ? new Date(r[0]).getTime() : -Infinity;
      var hi = r ? new Date(r[1]).getTime() : Infinity;
      var vals = [];
      for (var i = 0; i < cfg.t.length; i++) {
        if (cfg.t[i] >= lo && cfg.t[i] <= hi) vals.push(cfg.v[i]);
      }
      var m = median(vals);
      if (m === null) return;
      busy = true;
      var done = function () { busy = false; };
      var p = Plotly.relayout(gd, {
        "shapes[1].y0": m,
        "shapes[1].y1": m,
        "annotations[0].text": label(cfg.base, m, vals.length, cfg.suffix)
      });
      if (p && p.then) { p.then(done, done); } else { setTimeout(done, 0); }
    }

    gd.on("plotly_relayout", update);
    update();
  }

  function init() { CFG.forEach(attach); }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { setTimeout(init, 300); });
  } else {
    setTimeout(init, 300);
  }
})();
