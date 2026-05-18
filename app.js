/* ============================================================
   Wet Meat League Network — client app
   Hash router + view rendering. No framework, no build step.
   Phase A: placeholder views only. Phase B wires real data via
   lib/data.js (fetching content/*.json produced by the pipeline).
   ============================================================ */

/* ---------- tiny DOM helpers ---------- */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null) continue;
    node.append(c.nodeType ? c : document.createTextNode(c));
  }
  return node;
}

function placeholder(text) {
  return el("div", { class: "placeholder" }, text);
}

function sectionHeader(eyebrow, title) {
  return el(
    "div",
    {},
    el("span", { class: "eyebrow" }, eyebrow),
    el("h2", { class: "section-title section-title--accent" }, title)
  );
}

/* ---------- ticker ---------- */
const DEFAULT_TICKER = [
  "WET MEAT LEAGUE NETWORK",
  "Standings, power rankings & rosters — live from Sleeper",
  "2026 draft coverage incoming",
  "Trade wire & AI anchor desk: under construction",
];

function renderTicker(items = DEFAULT_TICKER) {
  const track = document.getElementById("ticker-track");
  if (!track) return;
  track.replaceChildren();
  // Duplicate the list so the CSS -50% loop is seamless.
  for (let pass = 0; pass < 2; pass++) {
    for (const text of items) {
      track.append(el("span", { class: "ticker__item" }, text));
    }
  }
}

/* ---------- views (Phase A placeholders) ---------- */
const Views = {
  home() {
    return el(
      "div",
      {},
      el(
        "div",
        { class: "hero" },
        el("span", { class: "eyebrow" }, "The Network"),
        el("h1", {}, "Wet Meat League Network"),
        el(
          "p",
          {},
          "Standings, power rankings, rosters, and breaking fantasy " +
            "non-news for the only league that matters."
        )
      ),
      el(
        "div",
        { class: "grid" },
        card("Standings", "The full table — wins, losses, and points of shame.", "#/standings"),
        card("Power Rankings", "Who's cooking and who's the wet meat. With commentary.", "#/power"),
        card("Teams", "Every roster, every owner, every questionable decision.", "#/teams"),
        card("2026 Draft", "Order, keepers, and a countdown to chaos.", "#/draft")
      )
    );
  },

  standings() {
    return section(
      "The Table",
      "Standings",
      "Standings load here once the data pipeline is wired (Phase B): " +
        "record, points for/against, and current streak for all 10 teams."
    );
  },

  power() {
    return section(
      "The Rankings",
      "Power Rankings",
      "A computed power ranking with an AI-written take on each team's " +
        "movement (Phase D). Until then: imagine confident nonsense here."
    );
  },

  teams() {
    return section(
      "The League",
      "Teams",
      "A grid of all 10 teams linking to individual team pages " +
        "(roster, owner, record). Wired in Phase B."
    );
  },

  team(params) {
    return section(
      "Team",
      "Team #" + params.id,
      "Individual team hub — roster, owner, record, and season narrative. " +
        "Wired in Phase B."
    );
  },

  draft() {
    return section(
      "2026 Draft",
      "Draft Hub",
      "Countdown, draft order, and keeper rules for the 2026 season, " +
        "pulled from Sleeper (Phase C). Flips to live results when it drafts."
    );
  },

  notFound() {
    return section(
      "404",
      "Off the Board",
      "That page doesn't exist. Even our intern wouldn't draft it."
    );
  },
};

function card(title, body, href) {
  return el(
    "a",
    { class: "card", href },
    el("h3", {}, title),
    el("p", {}, body)
  );
}

function section(eyebrow, title, body) {
  return el("div", {}, sectionHeader(eyebrow, title), placeholder(body));
}

/* ---------- router ---------- */
const ROUTES = [
  { pattern: "/", view: "home", title: "Home" },
  { pattern: "/standings", view: "standings", title: "Standings" },
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

function render() {
  const app = document.getElementById("app");
  const path = currentPath();
  const matched = matchRoute(path);
  const view = matched ? Views[matched.route.view] : Views.notFound;
  const params = matched ? matched.params : {};

  app.replaceChildren(view(params));
  document.title = matched
    ? `${matched.route.title} — WMLN`
    : "Not Found — WMLN";
  setActiveNav(path);
  window.scrollTo(0, 0);
}

/* ---------- init ---------- */
function init() {
  renderTicker();
  if (!location.hash) location.replace("#/");
  window.addEventListener("hashchange", render);
  render();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
