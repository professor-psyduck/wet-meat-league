/* ============================================================
   Wet Meat League Network — client app
   Hash router + views. ES module, no build step.
   Phase B: Standings / Teams / Team pages render REAL Sleeper
   data via lib/data.js. Power Rankings (Phase D) and the Draft
   hub (Phase C) are still placeholders.
   ============================================================ */

import {
  getManifest, getLeague, getStandings, getRosters, getPlayoffs,
  resolvePlayer, teamById,
} from "./lib/data.js";

/* ---------- tiny DOM helpers ---------- */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (v != null) node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

const fmtRecord = (w, l, t) => (t ? `${w}-${l}-${t}` : `${w}-${l}`);
const fmtPct = (p) => (p === 1 ? "1.000" : p.toFixed(3).replace(/^0/, ""));

function sectionHeader(eyebrow, title) {
  return el("div", {},
    el("span", { class: "eyebrow" }, eyebrow),
    el("h2", { class: "section-title section-title--accent" }, title));
}

function placeholder(text) {
  return el("div", { class: "placeholder" }, text);
}

function avatar(url, alt) {
  if (!url) return el("span", { class: "avatar avatar--blank", "aria-hidden": "true" });
  return el("img", { class: "avatar", src: url, alt: alt || "", loading: "lazy" });
}

function teamCell(name, url) {
  return el("span", { class: "teamcell" }, avatar(url, name), el("span", {}, name));
}

/* ---------- ticker ---------- */
const DEFAULT_TICKER = [
  "WET MEAT LEAGUE NETWORK",
  "Live from the only league that matters",
];

function renderTicker(items) {
  const track = document.getElementById("ticker-track");
  if (!track) return;
  const list = items && items.length ? items : DEFAULT_TICKER;
  track.replaceChildren();
  for (let pass = 0; pass < 2; pass++) {
    for (const text of list) {
      track.append(el("span", { class: "ticker__item" }, text));
    }
  }
}

async function refreshTicker() {
  try {
    const [m, s] = await Promise.all([getManifest(), getStandings()]);
    const top = s.standings.slice(0, 4)
      .map((r) => `#${r.rank} ${r.team_name} (${fmtRecord(r.wins, r.losses, r.ties)})`);
    const head = m.mode === "in_season"
      ? `${m.league_name} — ${m.data_season} season, week live`
      : `${m.league_name} — ${m.data_season} final standings`;
    const champLine = m.champion ? [`🏆 ${m.data_season} champion: ${m.champion}`] : [];
    renderTicker([head, ...champLine, ...top,
      `2026 draft: ${m.draft?.status || "tbd"}`]);
  } catch {
    renderTicker(DEFAULT_TICKER);
  }
}

/* ---------- views ---------- */
const Views = {
  async home() {
    const [m, s] = await Promise.all([getManifest(), getStandings()]);
    const sub = m.mode === "in_season"
      ? `${m.league_name} — ${m.data_season} season`
      : `Offseason desk — showing the ${m.data_season} final book while the ` +
        `2026 season loads. Draft status: ${m.draft?.status || "tbd"}.`;

    const wrap = el("div", {});
    wrap.append(
      el("div", { class: "hero" },
        el("span", { class: "eyebrow" }, "The Network"),
        el("h1", {}, "Wet Meat League Network"),
        el("p", {}, sub)));

    let champ = null;
    try {
      const p = await getPlayoffs();
      if (p.available) champ = p;
    } catch { /* playoffs optional */ }
    if (champ && champ.champion) {
      wrap.append(el("a", { class: "card card--champ", href: "#/playoffs" },
        el("span", { class: "badge" }, `${champ.season} Champion`),
        el("h3", {}, `🏆 ${champ.champion.team_name}`),
        el("p", {}, champ.runner_up
          ? `Beat ${champ.runner_up.team_name} in the final — see the full bracket →`
          : "See the full bracket →")));
    }

    const lead = s.standings[0];
    if (lead) {
      wrap.append(el("div", { class: "card card--lead" },
        el("span", { class: "badge" }, "Top of the table"),
        el("h3", {}, `${lead.team_name} — ${fmtRecord(lead.wins, lead.losses, lead.ties)}`),
        el("p", {}, `${lead.fpts} points for. Everyone else is wet meat.`)));
    }

    const mini = el("table", { class: "table" });
    mini.append(el("thead", {}, el("tr", {},
      el("th", {}, "#"), el("th", {}, "Team"),
      el("th", { class: "num" }, "Rec"), el("th", { class: "num" }, "PF"))));
    const tb = el("tbody", {});
    for (const r of s.standings.slice(0, 5)) {
      tb.append(el("tr", {},
        el("td", {}, String(r.rank)),
        el("td", {}, el("a", { href: `#/team/${r.roster_id}` },
          teamCell(r.team_name, r.avatar))),
        el("td", { class: "num" }, fmtRecord(r.wins, r.losses, r.ties)),
        el("td", { class: "num" }, String(r.fpts))));
    }
    mini.append(tb);
    wrap.append(sectionHeader("Standings", "Top 5"), mini,
      el("p", { class: "more" }, el("a", { href: "#/standings" }, "Full standings →")));
    return wrap;
  },

  async standings() {
    const s = await getStandings();
    const wrap = el("div", {}, sectionHeader(
      `${s.season} Season`, `${s.league_name} — Standings`));
    const table = el("table", { class: "table" });
    table.append(el("thead", {}, el("tr", {},
      el("th", {}, "#"), el("th", {}, "Team"), el("th", {}, "Manager"),
      el("th", { class: "num" }, "Rec"), el("th", { class: "num" }, "PCT"),
      el("th", { class: "num" }, "PF"), el("th", { class: "num" }, "PA"))));
    const tb = el("tbody", {});
    for (const r of s.standings) {
      tb.append(el("tr", {},
        el("td", {}, String(r.rank)),
        el("td", {}, el("a", { href: `#/team/${r.roster_id}` },
          teamCell(r.team_name, r.avatar))),
        el("td", {}, r.manager || "—"),
        el("td", { class: "num" }, fmtRecord(r.wins, r.losses, r.ties)),
        el("td", { class: "num" }, fmtPct(r.pct)),
        el("td", { class: "num" }, String(r.fpts)),
        el("td", { class: "num" }, String(r.fpts_against))));
    }
    table.append(tb);
    wrap.append(table);
    return wrap;
  },

  async playoffs() {
    const p = await getPlayoffs();
    if (!p.available) {
      return section("Playoffs", `${p.season || ""} Playoffs`,
        "The bracket isn't set yet. Check back once the playoffs begin.");
    }
    const wrap = el("div", {});
    const c = p.champion, r = p.runner_up, t3 = p.third;

    wrap.append(el("div", { class: "champ-banner" },
      el("span", { class: "champ-banner__trophy", "aria-hidden": "true" }, "🏆"),
      el("div", {},
        el("span", { class: "eyebrow" }, `${p.season} Champion`),
        el("h1", {}, c ? c.team_name : "—"),
        el("p", {}, c && r
          ? `Def. ${r.team_name}, ${fmtScore(c.score)}–${fmtScore(r.score)} in the final`
          : "Champion of the Wet Meat League"))));

    const podium = el("div", { class: "podium" });
    if (c) podium.append(podSpot("1st", c));
    if (r) podium.append(podSpot("2nd", r));
    if (t3) podium.append(podSpot("3rd", t3));
    wrap.append(podium);

    wrap.append(sectionHeader("The Bracket", "How it played out"));
    const bracket = el("div", { class: "bracket" });
    for (const rd of p.rounds) {
      const col = el("div", { class: "bracket__round" });
      col.append(el("h4", { class: "bracket__title" },
        `${rd.name}${rd.week ? ` · Wk ${rd.week}` : ""}`));
      for (const g of rd.games) col.append(gameCard(g));
      bracket.append(col);
    }
    wrap.append(bracket);
    return wrap;
  },

  async teams() {
    const lg = await getLeague();
    const wrap = el("div", {}, sectionHeader("The League", "Teams"));
    const grid = el("div", { class: "grid" });
    const teams = [...lg.teams].sort((a, b) =>
      b.wins - a.wins || b.fpts - a.fpts);
    for (const t of teams) {
      grid.append(el("a", { class: "card card--team", href: `#/team/${t.roster_id}` },
        el("div", { class: "card__head" },
          avatar(t.avatar, t.team_name),
          el("h3", {}, t.team_name)),
        el("p", {}, `${t.manager || "—"} · ${fmtRecord(t.wins, t.losses, t.ties)} · ${t.fpts} PF`)));
    }
    wrap.append(grid);
    return wrap;
  },

  async team(params) {
    const [lg, ro] = await Promise.all([getLeague(), getRosters()]);
    const t = teamById(lg, params.id);
    const roster = (ro.rosters || []).find((r) => r.roster_id === Number(params.id));
    if (!t || !roster) {
      return section("Team", "Unknown Team",
        "No team with that id in the current book.");
    }
    const wrap = el("div", {});
    wrap.append(el("div", { class: "hero hero--team" },
      avatar(t.avatar, t.team_name),
      el("div", {},
        el("span", { class: "eyebrow" }, `${ro.season} Season`),
        el("h1", {}, t.team_name),
        el("p", {}, `${t.manager || "—"} · ${fmtRecord(t.wins, t.losses, t.ties)} · ` +
          `${t.fpts} PF · ${t.fpts_against} PA`))));

    const startSet = new Set(roster.starters);
    const bench = roster.players.filter((p) => !startSet.has(p));
    wrap.append(rosterTable("Starters", roster.starters, lg));
    if (bench.length) wrap.append(rosterTable("Bench", bench, lg));
    wrap.append(el("p", { class: "more" },
      el("a", { href: "#/teams" }, "← All teams")));
    return wrap;
  },

  power() {
    return section("The Rankings", "Power Rankings",
      "Computed power ranking + an AI-written take on each team's movement " +
      "lands in the next build phase. For now, consult the standings.");
  },

  async draft() {
    let info = "";
    try {
      const m = await getManifest();
      info = ` Current 2026 status: ${m.draft?.status || "tbd"}.`;
    } catch { /* ignore */ }
    return section("2026 Draft", "Draft Hub",
      "Countdown, draft order, and keeper rules for the 2026 season are " +
      "coming next." + info);
  },

  notFound() {
    return section("404", "Off the Board",
      "That page doesn't exist. Even our intern wouldn't draft it.");
  },
};

const fmtScore = (v) => (v == null ? "—" : Number(v).toFixed(2));

function podSpot(place, team) {
  return el("div", { class: `pod pod--${place}` },
    el("span", { class: "pod__place" }, place),
    avatar(team.avatar, team.team_name),
    el("a", { class: "pod__name", href: `#/team/${team.roster_id}` },
      team.team_name));
}

function gameCard(g) {
  const tag = { 1: "Final", 3: "3rd Place", 5: "5th Place" }[g.placement];
  const row = (tm) => {
    const win = tm.roster_id != null && tm.roster_id === g.winner_roster_id;
    return el("div", { class: "bx-team" + (win ? " is-win" : "") },
      avatar(tm.avatar, tm.team_name),
      tm.roster_id != null
        ? el("a", { class: "bx-team__name", href: `#/team/${tm.roster_id}` }, tm.team_name)
        : el("span", { class: "bx-team__name" }, tm.team_name),
      el("span", { class: "bx-team__score" }, fmtScore(tm.score)));
  };
  return el("div", { class: "bx-game" },
    tag ? el("span", { class: "badge badge--muted bx-game__tag" }, tag) : null,
    row(g.t1), row(g.t2));
}

function rosterTable(title, ids, lg) {
  const table = el("table", { class: "table" });
  table.append(el("thead", {}, el("tr", {},
    el("th", {}, title), el("th", {}, "Pos"), el("th", {}, "NFL"))));
  const tb = el("tbody", {});
  for (const pid of ids) {
    const p = resolvePlayer(lg, pid);
    tb.append(el("tr", {},
      el("td", {}, p.n), el("td", {}, p.pos), el("td", {}, p.t || "—")));
  }
  table.append(tb);
  return el("div", {}, table);
}

function section(eyebrow, title, body) {
  return el("div", {}, sectionHeader(eyebrow, title), placeholder(body));
}

/* ---------- router ---------- */
const ROUTES = [
  { pattern: "/", view: "home", title: "Home" },
  { pattern: "/standings", view: "standings", title: "Standings" },
  { pattern: "/playoffs", view: "playoffs", title: "Playoffs" },
  { pattern: "/power", view: "power", title: "Power Rankings" },
  { pattern: "/teams", view: "teams", title: "Teams" },
  { pattern: "/team/:id", view: "team", title: "Team" },
  { pattern: "/draft", view: "draft", title: "2026 Draft" },
];

function matchRoute(path) {
  for (const route of ROUTES) {
    const pp = route.pattern.split("/").filter(Boolean);
    const ap = path.split("/").filter(Boolean);
    if (pp.length !== ap.length) continue;
    const params = {};
    let ok = true;
    for (let i = 0; i < pp.length; i++) {
      if (pp[i].startsWith(":")) params[pp[i].slice(1)] = decodeURIComponent(ap[i]);
      else if (pp[i] !== ap[i]) { ok = false; break; }
    }
    if (ok) return { route, params };
  }
  return null;
}

function currentPath() {
  const hash = location.hash.replace(/^#/, "");
  return hash.startsWith("/") ? hash : "/";
}

function setActiveNav(path) {
  const top = "/" + (path.split("/").filter(Boolean)[0] || "");
  document.querySelectorAll("#nav a").forEach((a) => {
    const isActive = a.getAttribute("data-route") === top;
    a.classList.toggle("is-active", isActive);
    if (isActive) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}

let _renderToken = 0;
async function render() {
  const app = document.getElementById("app");
  const path = currentPath();
  const matched = matchRoute(path);
  const view = matched ? Views[matched.route.view] : Views.notFound;
  const params = matched ? matched.params : {};

  setActiveNav(path);
  document.title = matched ? `${matched.route.title} — WMLN` : "Not Found — WMLN";
  app.replaceChildren(el("p", { class: "loading" }, "Loading…"));
  window.scrollTo(0, 0);

  const token = ++_renderToken;
  try {
    const node = await view(params);
    if (token !== _renderToken) return; // a newer navigation won
    app.replaceChildren(node);
  } catch (err) {
    if (token !== _renderToken) return;
    app.replaceChildren(section("Error", "Signal Lost",
      "Couldn't load league data. The pipeline may not have published yet. " +
      `(${err.message})`));
  }
}

/* ---------- init ---------- */
function init() {
  renderTicker(DEFAULT_TICKER);
  if (!location.hash) location.replace("#/");
  window.addEventListener("hashchange", render);
  render();
  refreshTicker();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
