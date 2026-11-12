// Markdown for AskNick answers, shared by guidebot.html and admin.html. Expects a global API ("" when served).
// ---------- markdown (escapes everything first, so model output can't inject HTML) ----------
const esc = s => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
function inline(s) {
  s = esc(s);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\[([^\]]+)\]\(((?:https?:\/\/|\/pdf\/)[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
  // guideline citations like (NonQM-Underwriting-Guidelines.pdf, §5.3, p. 42) open the PDF at that page
  s = s.replace(/\(([^()]*?([\w .&'-]+\.pdf)[^()]*?p\.\s*(\d+)[^()]*)\)/g,
    (m, inner, file, pg) => `<a class="cite" href="${API}/pdf/${encodeURIComponent(file.trim())}#page=${pg}" target="_blank">(${inner})</a>`);
  return s;
}
function md(src) {
  const lines = src.replace(/\r/g, "").split("\n");
  let out = "", para = [], i = 0;
  const flush = () => { if (para.length) { out += "<p>" + para.map(inline).join("<br>") + "</p>"; para = []; } };
  const cells = l => l.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { flush(); i++; continue; }
    let m;
    if ((m = l.match(/^(#{1,4})\s+(.*)/))) { flush(); out += `<h${m[1].length + 1}>${inline(m[2])}</h${m[1].length + 1}>`; i++; continue; }
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
        t += "<tr>" + cells(lines[i]).map(c => `<td>${inline(c)}</td>`).join("") + "</tr>"; i++;
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
        const top = stack[stack.length - 1];
        if (!top || top.depth < depth) { out += `<${tag}><li>`; stack.push({ depth, tag }); }
        else out += "</li><li>";
        out += inline(mm[3]); i++;
        while (i < lines.length && lines[i].trim() && !/^\s*([-*•]|\d+[.)])\s+/.test(lines[i]) && /^\s+/.test(lines[i])) {
          out += "<br>" + inline(lines[i].trim()); i++;
        }
      }
      while (stack.length) out += "</li></" + stack.pop().tag + ">";
      continue;
    }
    para.push(l); i++;
  }
  flush();
  return out;
}
