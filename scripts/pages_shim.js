// Static mode for the web demo (scripts/build_pages.py inlines this into the page): the page's /api requests are
// answered from the files the build wrote under data/, so the atlas runs on any static host with no server.
(function () {
  "use strict";
  var realFetch = window.fetch.bind(window), files = {};
  function load(path) {
    if (!files[path]) files[path] = realFetch(path).then(function (r) {
      if (!r.ok) throw new Error(path + ": " + r.status);
      return r.json();
    });
    return files[path];
  }
  function reply(body, status) {
    return new Response(JSON.stringify(body), {status: status || 200, headers: {"Content-Type": "application/json"}});
  }
  // FNV-1a over the id's characters: the build puts each event's text in the file this picks
  function shard(id, n) {
    var h = 2166136261;
    for (var i = 0; i < id.length; i++) h = Math.imul(h ^ id.charCodeAt(i), 16777619) >>> 0;
    return h % n;
  }
  var datasets = load("data/datasets.json");
  window.fetch = function (input, init) {
    var url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (url.origin !== location.origin || url.pathname.indexOf("/api/") !== 0) return realFetch(input, init);
    if (((init && init.method) || "GET").toUpperCase() !== "GET")
      return Promise.resolve(reply({error: "not available in the static demo"}, 403));
    var route = url.pathname.slice(5), q = url.searchParams;
    return datasets.then(function (list) {
      var ds = list.filter(function (d) { return d.id === q.get("ds"); })[0] || list[0], base = "data/" + ds.id + "/";
      if (route === "datasets") return reply(list);
      if (route === "features" || route === "feature_storylines" || route === "influence") return load(base + route + ".json").then(reply);
      if (route === "spread" || route === "flow") {
        var id = q.get("id");
        if (!id) return load(base + route + ".json").then(reply);
        return load(base + (route === "spread" ? "stretches" : "flow_cards") + ".json").then(function (m) {
          return reply(m[id] || {error: "not in the static demo"});
        });
      }
      if (route === "agent_sources") {
        var ids = (q.get("ids") || "").split(",").filter(Boolean).slice(0, 12);
        return Promise.all(ids.map(function (i) { return load(base + "events/" + shard(i, ds.shards) + ".json"); })).then(function (parts) {
          return reply(ids.map(function (i, k) { return parts[k][i]; }).filter(Boolean));
        });
      }
      return reply({error: "not available in the static demo"}, 404);
    }).catch(function (e) { return reply({error: String(e)}, 500); });
  };
})();
