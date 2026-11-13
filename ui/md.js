// Markdown for AskNick answers, shared by guidebot.html and admin.html. Expects a global API ("" when served).
// ---------- markdown (escapes everything first, so model output can't inject HTML) ----------
const esc = s => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
// ---------- program colors ----------
// Each product wears its paint from the Revo site's accent registry (revo-funding-site, scripts/pages/accents.json),
// using the "hero" tone that's lifted to read on a dark background. FHA has no hero tone there, so its color
// is FHA Teal lightened the same way. VA wears the flag's red, white and blue (its "hero edge" red and blue, also
// from accents.json): stripes on dots and bars, the red where one color is needed. Express and Ignite share a color.
const PROGRAMS = [
  ["second", /\b(2nd|second)\b/i, "#b884a3"],     // 2nd Liens, High at the Gala
  ["dscr", /\bDSCR\b/i, "#c3593c"],               // DSCR, Black-Orange
  ["bank", /bank[\s-]*statement/i, "#be2d5f"],    // Bank Statements, Lost Souls
  ["fulldoc", /full[\s-]*doc/i, "#d38645"],       // Full Doc, Midwest Cowboy
  ["1099", /\b1099\b/, "#6197c7"],                // 1099, London House
  ["pl", /P\s*&(?:amp;)?\s*L\b|profit/i, "#b78587"], // P&L, Rose After Dark
  // Agencies (Derrick's picks, 2026-11-12): Fannie blue, Freddie green, FHA green, VA red. The program sheets
  // and the agency guides both match, so a citation chip and its highlighted page wear the agency's color.
  ["fannie", /fannie|home[\s-]*ready|refi[\s-]*now/i, "#4a90d9"],                    // Fannie Mae blue, lifted
  ["freddie", /freddie|home[\s-]*possible|home[\s-]*one|refi[\s-]*possible/i, "#2fae6b"], // Freddie Mac green
  ["fha", /\bFHA\b|\bHUD\b|mortgagee[\s-]*letter/i, "#7cbf4f"],                      // FHA green
  ["va", /\bVA\b/, "#c8323f"],                    // flag red, hero edge (stripes below)
  ["conv", /conventional/i, "#e2d4ae"],           // Conventional sand (the tab; each program is Fannie or Freddie)
];
// One product named: its key. None, or several ("Ignite 1099, Ignite Bank Statements"): no color.
function progKey(text) {
  const hits = new Set(PROGRAMS.filter(([, re]) => re.test(text)).map(([k]) => k));
  if (hits.has("second")) return "second";   // "2nd Mortgage ... DSCR" is a 2nd
  if (hits.has("fannie") || hits.has("freddie")) hits.delete("conv");   // "Conventional HomeReady" is Fannie's
  return hits.size === 1 ? [...hits][0] : "";
}
// VA's dot is a tiny US flag: 13 stripes and a blue canton with a few stars (most blur out at 7px, that's fine).
const FLAG = `url("data:image/svg+xml,${encodeURIComponent(`<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 19 13'>`
  + `<path fill='#fff' d='M0 0h19v13H0z'/><path stroke='#c8323f' d='M0 .5h19M0 2.5h19M0 4.5h19M0 6.5h19M0 8.5h19M0 10.5h19M0 12.5h19'/>`
  + `<path fill='#3c5bb0' d='M0 0h8v7H0z'/><g fill='#fff'>${[[1.6, 1.4], [4, 1.4], [6.4, 1.4], [2.8, 3.5], [5.2, 3.5], [1.6, 5.6], [4, 5.6], [6.4, 5.6]]
    .map(([x, y]) => `<circle cx='${x}' cy='${y}' r='.5'/>`).join("")}</g></svg>`)}")`;
const progAttr = text => { const k = progKey(text); return k ? ` data-prog="${k}"` : ""; };
document.head.insertAdjacentHTML("beforeend", `<style>${PROGRAMS.map(([k, , c]) => `[data-prog="${k}"]{--pc:${c}}`).join("")}
[data-prog="va"]{--pdot:${FLAG} 0 0/100% 100% no-repeat;
  --pbar:linear-gradient(180deg,#4f79d8 0 30%,#c8323f 30% 40%,#fff 40% 50%,#c8323f 50% 60%,#fff 60% 70%,#c8323f 70% 80%,#fff 80% 90%,#c8323f 90%)}
.pdot[data-prog="va"],.md a.cite[data-prog="va"]::before,.md td[data-prog="va"]::before{width:10px;border-radius:1px}
.pdot{display:inline-block;flex:none;width:7px;height:7px;border-radius:2px;background:var(--pdot,var(--pc));margin-right:7px;vertical-align:0.08em}
.md a.cite[data-prog]::before,.md td[data-prog]::before{content:"";display:inline-block;width:6px;height:6px;border-radius:2px;
  background:var(--pdot,var(--pc));margin-right:5px;vertical-align:0.08em}
.md td[data-prog]::before{width:7px;height:7px;margin-right:7px}
.md li.row[data-prog]{background:var(--pbar,linear-gradient(var(--pc),var(--pc))) no-repeat 0 0/3px 100%;padding-left:12px}</style>`);

// Citations like (NonQM-Underwriting-Guidelines.pdf, section 7.2, p. 53; Ignite 1099 Expanded.pdf, p. 2) become
// small chips, "NonQM Guide · §7.2 p. 53", one per source. Each opens the PDF at that page; the full citation
// shows on hover. A long filename in every sentence made answers hard to read.
const shortName = f => f.replace(/\.pdf$/i, "").replace(/ \d{4}-\d{2}-\d{2}$/, "")   // the edition date is on hover
  .replace(/[-_]+/g, " ").replace(/\b(Matrix|Expanded)\b/gi, "")
  .replace(/NonQM Underwriting Guidelines/i, "NonQM Guide").replace(/Fannie Mae Selling Guide/i, "Fannie Selling Guide")
  .replace(/Freddie Mac Seller Servicer Guide/i, "Freddie Guide").replace(/\s{2,}/g, " ").trim();
function citeChips(inner) {
  let lastFile = "";
  return inner.split(/;\s*/).map(part => {
    // no colon in a filename: "(All three matrices state the same tables: VA Standard Matrix.pdf, p. 6)" linked to a
    // file named after the whole sentence
    // nor a leading "also" / "see": "(also Premier-2nd-Mortgage-UW-Guidelines.pdf, p. 14)"
    const file = (part.match(/([^,;():]+?\.pdf)/i) || [])[1]?.trim().replace(/^(also|see|and|plus|per|via)\s+/i, "") || lastFile;
    if (!file) return "";
    lastFile = file;
    const pg = (part.match(/p(?:age|p)?\.?\s*(\d+)/i) || [])[1];
    const sec = (part.match(/(?:section|§)\s*([\d.]+\d)/i) || [])[1];
    const label = shortName(file) + (sec || pg ? " · " : "") + (sec ? `§${sec} ` : "") + (pg ? `p. ${pg}` : "");
    // "mark x0 y0 x1 y1 ...": a matrix row's exact place on the page, from the Python lookup (conv_matrix.cite)
    const mark = (part.match(/,\s*mark ([\d. ]+)/) || [])[1]?.trim();
    return `<a class="cite"${progAttr(file)} href="${API}/pdf/${encodeURIComponent(file)}${pg ? "#page=" + pg : ""}" target="_blank"
      ${mark ? `data-mark="${esc(mark)}" ` : ""}title="${esc(part.replace(/,\s*mark [\d. ]+/, "").trim())}">${esc(label.trim())}</a>`;
  }).join("");
}
function inline(s) {
  const chips = [];   // pulled out before escaping so "&" in a filename doesn't split the list
  s = s.replace(/\(([^()]*\.pdf[^()]*)\)/gi, (m, inner) => {
    if (/^\s*(\/|https?:)/.test(inner)) return m;   // the URL half of a [text](/pdf/x.pdf) link
    chips.push(citeChips(inner));
    return `\u0000${chips.length - 1}\u0000`;
  });
  s = s.replace(/\s*(\u0000\d+\u0000)([.,;:]+)/g, "$2$1");   // "rule (cite)." reads as "rule. [chip]"
  s = esc(s);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\[([^\]]+)\]\(((?:https?:\/\/|\/pdf\/)[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
  return s.replace(/\s*\u0000(\d+)\u0000/g, (m, i) => " " + chips[i]);
}
function md(src) {
  const lines = src.replace(/\r/g, "").split("\n");
  let out = "", para = [], i = 0;
  // A paragraph that is only bold ("**Programs with a minimum score:**") is a section header, like the guides'.
  const flush = () => {
    if (!para.length) return;
    const h = para.length === 1 && para[0].match(/^\s*\*\*([^*]+?):?\*\*:?\s*$/);
    out += h ? `<h3 class="sec">${inline(h[1])}</h3>` : "<p>" + para.map(inline).join("<br>") + "</p>";
    para = [];
  };
  const cells = l => l.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { flush(); i++; continue; }
    let m;
    if ((m = l.match(/^(#{1,4})\s+(.*)/))) { flush(); out += `<h${m[1].length + 1} class="sec">${inline(m[2])}</h${m[1].length + 1}>`; i++; continue; }
    if (/^\s*>/.test(l)) {
      flush();
      const q = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) q.push(lines[i++].replace(/^\s*>\s?/, ""));
      out += "<blockquote>" + md(q.join("\n")) + "</blockquote>"; continue;
    }
    if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      flush();
      let t = "<div class='tbl'><table><thead><tr>" + cells(l).map(c => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>";
      i += 2;
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        // The first column names the program in comparison tables: give it the program's color dot.
        // (A cell that is a citation already wears the dot on its chip.)
        t += "<tr>" + cells(lines[i]).map((c, j) => `<td${j || /\.pdf\b/i.test(c) ? "" : progAttr(c)}>${inline(c)}</td>`).join("") + "</tr>"; i++;
      }
      out += t + "</tbody></table></div>"; continue;
    }
    if (/^\s*([-*•]|\d+[.)])\s+/.test(l)) {
      flush();
      const stack = [];
      while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) {
        const mm = lines[i].match(/^(\s*)([-*•]|\d+[.)])\s+(.*)/);
        const depth = Math.floor(mm[1].replace(/\t/g, "    ").length / 2), tag = /\d/.test(mm[2]) ? "ol" : "ul";
        while (stack.length && stack[stack.length - 1].depth > depth) out += "</li></" + stack.pop().tag + ">";
        // "**Term:** rule" items read like a guideline table: term on the left, rule on the right.
        const row = mm[3].match(/^\*\*([^*]+?)(?::\*\*|\*\*:)\s*(.*)$/), li = row ? `<li class="row"${progAttr(row[1])}>` : "<li>";
        const top = stack[stack.length - 1];
        if (!top || top.depth < depth) { out += `<${tag}>${li}`; stack.push({ depth, tag }); }
        else out += "</li>" + li;
        out += row ? `<span class="term">${inline(row[1])}</span><span class="def">${inline(row[2])}` : inline(mm[3]); i++;
        while (i < lines.length && lines[i].trim() && !/^\s*([-*•]|\d+[.)])\s+/.test(lines[i]) && /^\s+/.test(lines[i])) {
          out += "<br>" + inline(lines[i].trim()); i++;
        }
        if (row) out += "</span>";
      }
      while (stack.length) out += "</li></" + stack.pop().tag + ">";
      continue;
    }
    para.push(l); i++;
  }
  flush();
  return out;
}
