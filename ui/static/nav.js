// One navigation bar for every page: citizen pages on the left, investigator pages on the right.
// Loaded with <script src="/static/nav.js">; it also pulls in the shared stylesheet so a page only needs this one line.
(function () {
  const CITIZEN = [["/", "Check"], ["/guard", "Call guard"], ["/help", "Private help"], ["/my-investigations", "My investigations"]];
  const INVESTIGATOR = [["/command", "Command centre"], ["/officials", "What is spreading"]];
  if (!document.querySelector('link[href="/static/jv.css"]')) {
    const l = document.createElement("link"); l.rel = "stylesheet"; l.href = "/static/jv.css"; document.head.prepend(l);
  }
  const nav = document.createElement("nav");
  nav.className = "jnav";
  const link = (h, t) => { const a = document.createElement("a"); a.href = h; a.textContent = t; if (location.pathname === h || (h !== "/" && location.pathname.startsWith(h))) a.className = "on"; return a; };
  const brand = document.createElement("b");
  brand.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2 4 5v6c0 5 3.4 9.3 8 11 4.6-1.7 8-6 8-11V5l-8-3Z" fill="var(--accent)"/><path d="m8.5 12 2.5 2.5 4.5-5" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>SATYASUTRA';
  nav.append(brand, ...CITIZEN.map((x) => link(...x)));
  const sp = document.createElement("span"); sp.className = "sp"; nav.append(sp);
  const tag = document.createElement("span"); tag.className = "tagm"; tag.textContent = "Investigator"; nav.append(tag, ...INVESTIGATOR.map((x) => link(...x)));
  document.body.prepend(nav);
})();
