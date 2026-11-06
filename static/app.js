// Search and pay-grade highlight. No analytics. No cookies.
(function () {
  var base = window.SITE_BASE || "";
  var indexPromise = null;

  function loadIndex() {
    if (!indexPromise) {
      indexPromise = fetch(base + "/static/search.json")
        .then(function (res) {
          if (!res.ok) throw new Error("search index");
          return res.json();
        })
        .then(function (data) {
          var byCode = {};
          data.areas.forEach(function (area) {
            if (area.k !== "alias") byCode[area.c] = area;
          });
          data.byCode = byCode;
          return data;
        });
    }
    return indexPromise;
  }

  function resultLink(href, title, meta) {
    var li = document.createElement("li");
    var a = document.createElement("a");
    a.href = href;
    a.textContent = title;
    if (meta) {
      var span = document.createElement("span");
      span.className = "meta";
      span.textContent = meta;
      a.appendChild(span);
    }
    li.appendChild(a);
    return li;
  }

  function areaHref(area) {
    if (area.k === "alias" || area.s) {
      return base + "/duty-station/" + area.s + "/";
    }
    return base + "/mha/" + area.c.toLowerCase() + "/";
  }

  function search(data, raw) {
    var q = raw.trim().toLowerCase();
    var out = [];
    if (!q) return out;
    var seen = {};
    function add(key, node) {
      if (seen[key] || out.length >= 12) return;
      seen[key] = true;
      out.push(node);
    }
    if (/^\d{5}$/.test(q)) {
      var code = null;
      for (var i = 0; i < data.zips.length; i++) {
        if (data.zips[i][0] === q) { code = data.zips[i][1]; break; }
      }
      if (code && data.byCode[code]) {
        var area = data.byCode[code];
        add("z" + q, resultLink(base + "/mha/" + area.c.toLowerCase() + "/", "ZIP " + q, area.n + " · " + area.c));
      } else if (code) {
        add("z" + q, resultLink(base + "/zip/", "ZIP " + q, code + " has no BAH rate row"));
      } else {
        add("missing" + q, resultLink(base + "/zip/", "ZIP " + q + " is not in the DoD file", "Browse ZIP codes"));
      }
    } else if (/^\d{2,4}$/.test(q)) {
      var n = 0;
      for (var j = 0; j < data.zips.length && n < 8; j++) {
        if (data.zips[j][0].indexOf(q) === 0) {
          var zc = data.zips[j][1];
          var name = data.byCode[zc] ? data.byCode[zc].n : zc;
          var zhref = data.byCode[zc] ? base + "/mha/" + zc.toLowerCase() + "/" : base + "/zip/";
          add("z" + data.zips[j][0], resultLink(zhref, "ZIP " + data.zips[j][0], name));
          n++;
        }
      }
    }
    data.areas.forEach(function (area) {
      var hay = (area.n + " " + area.c).toLowerCase();
      if (hay.indexOf(q) === -1 && area.c.toLowerCase() !== q) return;
      var kind = area.k === "county" ? "County cost group" : (area.k === "alias" ? "Also called" : "Duty station");
      add("a" + area.s + area.n, resultLink(areaHref(area), area.n, kind + " · " + area.c));
      if (area.c.toLowerCase() === q && area.k !== "alias") {
        add("m" + area.c, resultLink(base + "/mha/" + area.c.toLowerCase() + "/", "Housing area " + area.c, area.n));
      }
    });
    return out;
  }

  function bindLookup(form) {
    var input = form.querySelector("[data-lookup]");
    var list = form.querySelector(".lookup-results");
    if (!input || !list) return;
    var timer = null;
    function render(items, message) {
      list.textContent = "";
      if (message) {
        var li = document.createElement("li");
        li.textContent = message;
        li.style.padding = "8px 10px";
        list.appendChild(li);
      }
      items.forEach(function (node) { list.appendChild(node); });
      list.hidden = !message && items.length === 0 && !input.value.trim();
      if (!input.value.trim()) list.hidden = true;
    }
    function run() {
      var value = input.value;
      if (!value.trim()) { render([], ""); return; }
      render([], "Loading the ZIP index…");
      loadIndex().then(function (data) {
        if (input.value !== value) return;
        var items = search(data, value);
        render(items, items.length ? "" : "No match in the DoD file.");
      }).catch(function () {
        render([], "The ZIP index did not load. Browse housing areas instead.");
      });
    }
    input.addEventListener("focus", function () { loadIndex().catch(function () {}); });
    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(run, 80);
    });
    form.addEventListener("submit", function (event) {
      var value = input.value.trim();
      if (/^\d{5}$/.test(value)) {
        event.preventDefault();
        window.location.href = base + "/zip/" + value + "/";
      } else if (value) {
        event.preventDefault();
        run();
      }
    });
    document.addEventListener("click", function (event) {
      if (!form.contains(event.target)) list.hidden = true;
    });
  }

  document.querySelectorAll("form.lookup").forEach(bindLookup);

  document.querySelectorAll(".grade-pick").forEach(function (sel) {
    var table = document.getElementById(sel.getAttribute("data-table"));
    if (!table) return;
    sel.addEventListener("change", function () {
      var grade = sel.value;
      table.querySelectorAll("tbody tr[data-grade]").forEach(function (row) {
        row.classList.toggle("hit", !!grade && row.getAttribute("data-grade") === grade);
      });
    });
  });

  document.querySelectorAll(".search.solo").forEach(function (input) {
    var items = document.querySelectorAll(input.getAttribute("data-filter-target"));
    input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase();
      items.forEach(function (el) {
        var hay = el.getAttribute("data-q") || "";
        var hide = q && hay.indexOf(q) === -1;
        el.parentElement.hidden = hide;
      });
    });
  });
})();
