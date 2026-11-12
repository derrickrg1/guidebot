// Markdown for AskNick answers, shared by guidebot.html and admin.html. Expects a global API ("" when served).
// ---------- markdown (escapes everything first, so model output can't inject HTML) ----------
const esc = s => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
// ---------- program colors ----------
// Each product wears its paint from the Revo site's accent registry (revo-funding-site, scripts/pages/accents.json),
// using the "hero" tone that's lifted to read on a dark background. FHA and VA have no hero tone there, so theirs
// are FHA Teal and Military Brown lightened the same way. Express and Ignite share a product's color.
const PROGRAMS = [
  ["second", /\b(2nd|second)\b/i, "#b884a3"],     // 2nd Liens, High at the Gala
  ["dscr", /\bDSCR\b/i, "#c3593c"],               // DSCR, Black-Orange
  ["bank", /bank[\s-]*statement/i, "#be2d5f"],    // Bank Statements, Lost Souls
  ["fulldoc", /full[\s-]*doc/i, "#d38645"],       // Full Doc, Midwest Cowboy
  ["1099", /\b1099\b/, "#6197c7"],                // 1099, London House
  ["pl", /P\s*&(?:amp;)?\s*L\b|profit/i, "#b78587"], // P&L, Rose After Dark
  ["fha", /\bFHA\b/, "#4f9ba6"],                  // FHA Teal #1c5d67, lifted
  ["va", /\bVA\b/, "#ad9361"],                    // Military Brown #62502b, lifted
  ["conv", /conventional/i, "#e2d4ae"],           // Conventional sand
];
// One product named: its key. None, or several ("Ignite 1099, Ignite Bank Statements"): no color.
function progKey(text) {
  const hits = new Set(PROGRAMS.filter(([, re]) => re.test(text)).map(([k]) => k));
  if (hits.has("second")) return "second";   // "2nd Mortgage ... DSCR" is a 2nd
  return hits.size === 1 ? [...hits][0] : "";
}
const progAttr = text => { const k = progKey(text); return k ? ` data-prog="${k}"` : ""; };
document.head.insertAdjacentHTML("beforeend", `<style>${PROGRAMS.map(([k, , c]) => `[data-prog="${k}"]{--pc:${c}}`).join("")}
.pdot{display:inline-block;flex:none;width:7px;height:7px;border-radius:2px;background:var(--pc);margin-right:7px;vertical-align:0.08em}
.md a.cite[data-prog]::before,.md td[data-prog]::before{content:"";display:inline-block;width:6px;height:6px;border-radius:2px;
  background:var(--pc);margin-right:5px;vertical-align:0.08em}
.md td[data-prog]::before{width:7px;height:7px;margin-right:7px}
.md li.row[data-prog]{box-shadow:inset 3px 0 0 var(--pc);padding-left:12px}</style>`);

// Citations like (NonQM-Underwriting-Guidelines.pdf, section 7.2, p. 53; Ignite 1099 Expanded.pdf, p. 2) become
// small chips, "NonQM Guide · §7.2 p. 53", one per source. Each opens the PDF at that page; the full citation
// shows on hover. A long filename in every sentence made answers hard to read.
const shortName = f => f.replace(/\.pdf$/i, "").replace(/[-_]+/g, " ").replace(/\b(Matrix|Expanded)\b/gi, "")
  .replace(/NonQM Underwriting Guidelines/i, "NonQM Guide").replace(/\s{2,}/g, " ").trim();
function citeChips(inner) {
  let lastFile = "";
  return inner.split(/;\s*/).map(part => {
    const file = (part.match(/([^,;()]+?\.pdf)/i) || [])[1]?.trim() || lastFile;
    if (!file) return "";
    lastFile = file;
    const pg = (part.match(/p(?:age|p)?\.?\s*(\d+)/i) || [])[1];
    const sec = (part.match(/(?:section|§)\s*([\d.]+\d)/i) || [])[1];
    const label = shortName(file) + (sec || pg ? " · " : "") + (sec ? `§${sec} ` : "") + (pg ? `p. ${pg}` : "");
    return `<a class="cite"${progAttr(file)} href="${API}/pdf/${encodeURIComponent(file)}${pg ? "#page=" + pg : ""}" target="_blank"
      title="${esc(part.trim())}">${esc(label.trim())}</a>`;
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
        t += "<tr>" + cells(lines[i]).map((c, j) => `<td${j ? "" : progAttr(c)}>${inline(c)}</td>`).join("") + "</tr>"; i++;
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
