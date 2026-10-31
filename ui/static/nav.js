// One navigation bar for every page: citizen pages on the left, investigator pages on the right. Loaded with <script src="/static/nav.js">.
(function () {
  const CITIZEN = [["/", "Check"], ["/my-investigations", "My investigations"], ["/about", "How it works"]];
  const INVESTIGATOR = [["/command", "Command centre"], ["/officials", "What is spreading"]];
  const css = document.createElement("style");
  css.textContent = ".jnav{display:flex;flex-wrap:wrap;gap:4px 14px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line,#dde2ee);background:var(--panel,#fff);font:14px system-ui,sans-serif}" +
    ".jnav b{letter-spacing:.14em;margin-right:8px;color:var(--text,#131a2a)}.jnav a{color:var(--mute,#5d6885);text-decoration:none;padding:3px 2px;border-bottom:2px solid transparent}" +
    ".jnav a.on{color:var(--text,#131a2a);border-color:var(--accent,#2f5bd8)}.jnav .sp{flex:1}.jnav .tagm{font-size:11px;color:var(--mute,#5d6885);border:1px solid var(--line,#dde2ee);border-radius:999px;padding:1px 8px}" +
    "@media print{.jnav{display:none}}";
  document.head.append(css);
  const nav = document.createElement("nav");
  nav.className = "jnav";
  const link = (h, t) => { const a = document.createElement("a"); a.href = h; a.textContent = t; if (location.pathname === h || (h !== "/" && location.pathname.startsWith(h))) a.className = "on"; return a; };
  const brand = document.createElement("b"); brand.textContent = "SATYASUTRA";
  nav.append(brand, ...CITIZEN.map((x) => link(...x)));
  const sp = document.createElement("span"); sp.className = "sp"; nav.append(sp);
  const tag = document.createElement("span"); tag.className = "tagm"; tag.textContent = "Investigator"; nav.append(tag, ...INVESTIGATOR.map((x) => link(...x)));
  document.body.prepend(nav);
})();
