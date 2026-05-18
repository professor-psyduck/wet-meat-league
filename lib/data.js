/* ============================================================
   Data layer — fetches content/*.json produced by the pipeline.
   ES module. Caches responses so views can re-render freely.
   Paths are resolved relative to the page directory so it works
   both at /wet-meat-league/ (Pages) and /index.html (local).
   ============================================================ */

const ROOT = location.pathname.replace(/[^/]*$/, "");
const _cache = new Map();

async function getJSON(relPath) {
  if (_cache.has(relPath)) return _cache.get(relPath);
  const res = await fetch(ROOT + relPath, { cache: "no-cache" });
  if (!res.ok) throw new Error(`Failed to load ${relPath} (HTTP ${res.status})`);
  const data = await res.json();
  _cache.set(relPath, data);
  return data;
}

export function clearCache() { _cache.clear(); }

export const getManifest  = () => getJSON("content/manifest.json");
export const getLeague    = () => getJSON("content/league.json");
export const getStandings = () => getJSON("content/standings.json");
export const getRosters   = () => getJSON("content/rosters.json");
export const getPlayoffs  = () => getJSON("content/playoffs.json");
export const getPower     = () => getJSON("content/power.json");
export const getDraft     = () => getJSON("content/draft.json");

/** Resolve a Sleeper player id to {n, pos, t} via league.json's slim map. */
export function resolvePlayer(league, pid) {
  return (league.players && league.players[pid]) ||
         { n: pid, pos: "?", t: "" };
}

/** Look up a team in league.teams by roster_id. */
export function teamById(league, rosterId) {
  const id = Number(rosterId);
  return (league.teams || []).find((t) => t.roster_id === id) || null;
}
