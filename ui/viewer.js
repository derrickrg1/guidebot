// Guideline panel for guidebot.html: draws the cited PDF with PDF.js beside the chat and marks the lines the
// answer is citing. Works the same in every browser (the built-in PDF viewer doesn't on iPhone). If PDF.js can't
// load, it falls back to the browser's own viewer in an iframe.
//
// Finding the line: the answer paraphrases, so this matches the cited sentence's numbers and key words against
// each line of the cited page. Numbers count most ("9 months", "$1,000,001 to $2,000,000"), which lands on the
// right matrix row even when the words differ. Nothing is guessed: if no line scores well enough, nothing is
// marked and the header says so.
const PDFJS = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/";
const V = { doc: null, file: "", zoom: 1, fit: 1, n: 0, token: 0, marks: null, note: "", observer: null, scaleKey: "" };
const vel = id => document.getElementById(id);
let pdfjsReady = null;

function loadPdfjs() {
  pdfjsReady ||= new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = PDFJS + "pdf.min.js";
    s.onload = async () => {
      try {
        // A worker script from another site can't be started directly, so it's fetched and started from a blob.
        const code = await (await fetch(PDFJS + "pdf.worker.min.js")).text();
        window.pdfjsLib.GlobalWorkerOptions.workerSrc = URL.createObjectURL(new Blob([code], { type: "text/javascript" }));
        ok(window.pdfjsLib);
      } catch (e) { fail(e); }
    };
    s.onerror = () => fail(new Error("PDF.js didn't load"));
    document.head.appendChild(s);
  }).catch(e => { pdfjsReady = null; throw e; });
  return pdfjsReady;
}

// ---------- what the answer says next to the clicked chip ----------
function textBefore(block, link) {
  // The sentence a chip belongs to: the text since the previous sentence's chips. Several chips in a row
  // (one sentence citing several PDFs) all share the sentence before them.
  let seg = "", last = "", hit = null;
  const walk = node => {
    for (const n of node.childNodes) {
      if (hit !== null) return;
      if (n.nodeType === 3) { seg += n.textContent; continue; }
      if (n.nodeType !== 1 || n.matches("ul, ol")) continue;
      if (n.matches("a.cite")) {
        if (n === link) { hit = seg.trim() ? seg : last; return; }
        if (seg.trim()) { last = seg; seg = ""; }
        continue;
      }
      walk(n);
    }
  };
  walk(block);
  return hit ?? seg;
}
function claimFor(link) {
  const src = link.closest(".srcitem");
  if (src) return src.querySelector("blockquote")?.textContent || "";   // a real quote from the page
  const row = link.closest("tr");
  if (row) return [...row.children].filter(td => !td.querySelector("a.cite")).map(td => td.textContent).join(" ");
  const block = link.closest("li, p");
  return block ? textBefore(block, link) : "";
}

// ---------- matching ----------
const STOP = new Set(("the and for with from that this must have will are not any per all its than into each "
  + "when only also does other such been more which their there them they shall may can").split(" "));
function tokens(text) {
  let t = text.toLowerCase();
  for (let prev; prev !== t;) { prev = t; t = t.replace(/(\d),(\d{3})/g, "$1$2"); }   // 1,000,001 -> 1000001
  const nums = t.match(/\d+(?:\.\d+)?/g) || [];
  // Rough stems so the answer's wording meets the page's: experienced/experience, investors/investor.
  const words = (t.match(/[a-z][a-z']{3,}/g) || []).filter(w => !STOP.has(w))
    .map(w => w.length > 6 ? w.slice(0, 6) : w.replace(/(es|s)$/, ""));
  return { nums: new Set(nums), words: new Set(words) };
}
function score(claim, text) {
  const t = tokens(text);
  let s = 0;
  for (const n of t.nums) if (claim.nums.has(n)) s += n.replace(".", "").length > 2 ? 4 : 2;
  for (const w of t.words) if (claim.words.has(w)) s += 1;
  return s;
}
async function findMarks(page, claimText, min = 4) {
  // Lines of the page: text runs on the same baseline. Returns rectangles in PDF units around the matching
  // part of the best lines, from the first matching run to the last, so the other column of a two-column
  // sheet isn't swept in.
  const claim = tokens(claimText);
  if (!claim.nums.size && claim.words.size < 3) return [];
  const lines = [];
  for (const it of (await page.getTextContent()).items) {
    if (!it.str || !it.str.trim()) continue;
    const y = it.transform[5], h = Math.hypot(it.transform[2], it.transform[3]) || it.height || 8;
    let line = lines.find(l => Math.abs(l.y - y) < Math.max(1.5, h * 0.35));
    if (!line) lines.push(line = { y, items: [] });
    line.items.push({ it, h, s: 0 });
  }
  lines.sort((a, b) => b.y - a.y);   // top of the page first
  for (const l of lines) {
    l.items.sort((a, b) => a.it.transform[4] - b.it.transform[4]);
    l.items.forEach(x => { x.s = score(claim, x.it.str); });
    l.text = l.items.map(x => x.it.str).join(" ");
    l.score = score(claim, l.text);
  }
  // A sentence of prose wraps over several lines, so runs of 2 or 3 lines are scored too. A small penalty per
  // extra line keeps a matrix row (everything on one line) winning as a single line.
  let win = null;
  for (let i = 0; i < lines.length; i++) {
    for (let size = 1; size <= 3 && i + size <= lines.length; size++) {
      const run = lines.slice(i, i + size), sc = score(claim, run.map(l => l.text).join(" "));
      const adj = sc / (1 + 0.15 * (size - 1));
      if (!win || adj > win.adj) win = { run, sc, adj };
    }
  }
  // A quote in the answer is the page's own words: a line holding it wins over word overlap. "Per DU" scores
  // nothing as words (stop word, too short), and the Fannie matrix's rental income row took the mark instead.
  const flat = s => s.toLowerCase().replace(/[“”"]/g, "").replace(/\s+/g, " ").trim();
  const quotes = [...claimText.matchAll(/["“]([^"”]{2,160})["”]/g)].map(m => flat(m[1])).reverse();
  for (const q of quotes) {
    const has = lines.filter(l => flat(l.text).includes(q));
    if (has.length) {
      const l = has.sort((a, b) => b.score - a.score)[0];
      const xs = l.items.flatMap(x => [x.it.transform[4], x.it.transform[4] + x.it.width]);
      const h = Math.max(...l.items.map(x => x.h));
      return [[Math.min(...xs), l.y - h * 0.25, Math.max(...xs), l.y + h * 0.95]];
    }
  }
  if (!win || win.sc < min) return [];
  // A single best line can have equally good matches elsewhere on the page (the same tier in two tables).
  const picked = win.run.length > 1 ? win.run
    : lines.filter(l => l.score >= Math.max(min, win.sc * 0.75)).sort((a, b) => b.score - a.score).slice(0, 3);
  return picked.filter(l => l.items.some(x => x.s > 0)).map(l => {
    const hits = l.items.filter(x => x.s > 0);
    const span = l.items.slice(l.items.indexOf(hits[0]), l.items.indexOf(hits[hits.length - 1]) + 1);
    const xs = span.flatMap(x => [x.it.transform[4], x.it.transform[4] + x.it.width]);
    const h = Math.max(...span.map(x => x.h));
    return [Math.min(...xs), l.y - h * 0.25, Math.max(...xs), l.y + h * 0.95];
  });
}

// ---------- drawing ----------
function scale() { return V.fit * V.zoom; }
async function layout() {
  // One placeholder per page sized from page 1, corrected as each page is drawn. Pages draw when scrolled near.
  const box = vel("vpages");
  const first = await V.doc.getPage(1), base = first.getViewport({ scale: 1 });
  V.fit = Math.max(0.3, (box.clientWidth - 24) / base.width);
  V.fitWidth = vel("viewer").getBoundingClientRect().width;   // the panel, not the page list: its scrollbar comes and goes
  V.scaleKey = String(scale());
  const vp = first.getViewport({ scale: scale() });
  V.observer?.disconnect();
  V.observer = new IntersectionObserver(es => es.forEach(e => e.isIntersecting && drawPage(+e.target.dataset.n).catch(() => {})),
                                        { root: box, rootMargin: "800px 0px" });
  box.innerHTML = "";
  for (let n = 1; n <= V.n; n++) {
    const d = document.createElement("div");
    d.className = "vpg"; d.dataset.n = n;
    d.style.width = vp.width + "px"; d.style.height = vp.height + "px";
    d.innerHTML = `<span class="num">${n}</span>`;
    box.appendChild(d);
    V.observer.observe(d);
  }
}
async function drawPage(n) {
  const d = vel("vpages").querySelector(`.vpg[data-n="${n}"]`);
  if (!d || d.dataset.drawn === V.scaleKey) return d;
  d.dataset.drawn = V.scaleKey;
  const page = await V.doc.getPage(n), vp = page.getViewport({ scale: scale() });
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const c = document.createElement("canvas");
  c.width = Math.floor(vp.width * dpr); c.height = Math.floor(vp.height * dpr);
  d.style.width = vp.width + "px"; d.style.height = vp.height + "px";
  try {
    await page.render({ canvasContext: c.getContext("2d"), viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null }).promise;
  } catch (e) {
    delete d.dataset.drawn;   // cancelled or failed: let it draw again next time it's scrolled near
    throw e;
  }
  d.querySelector("canvas")?.remove();
  d.prepend(c);
  if (V.marks && V.marks.page === n) {
    d.querySelectorAll(".vhl").forEach(x => x.remove());
    for (const r of V.marks.rects) {
      const [x1, y1, x2, y2] = vp.convertToViewportRectangle(r);
      const m = document.createElement("div");
      m.className = "vhl";
      Object.assign(m.style, { left: Math.min(x1, x2) - 3 + "px", top: Math.min(y1, y2) - 2 + "px",
                               width: Math.abs(x2 - x1) + 6 + "px", height: Math.abs(y2 - y1) + 4 + "px" });
      d.appendChild(m);
    }
  }
  return d;
}
function currentPage() {
  const box = vel("vpages"), mid = box.scrollTop + box.clientHeight / 3;
  let n = 1;
  for (const d of box.children) { if (d.offsetTop <= mid) n = +d.dataset.n; else break; }
  return n;
}
function setHeader(page, note = page === V.marks?.page ? V.note : "") {   // the marked page keeps its note
  vel("vpage").textContent = `Page ${page} of ${V.n || "?"} · ${V.file}` + (note ? ` · ${note}` : "");
}

// ---------- open / close ----------
async function openGuideline(link) {
  const u = new URL(link.href, location.href);
  const file = decodeURIComponent(u.pathname.split("/pdf/")[1] || ""), page = +((u.hash.match(/page=(\d+)/) || [])[1] || 1);
  const url = `${API}/pdf/${encodeURIComponent(file)}`, token = ++V.token;
  const k = progKey(file);
  if (k) vel("viewer").dataset.prog = k; else delete vel("viewer").dataset.prog;   // the marker takes the agency's color
  vel("vname").innerHTML = (k ? `<i class="pdot" data-prog="${k}"></i>` : "") + esc(shortName(file));
  vel("vpage").textContent = `Page ${page} · ${file}`;
  vel("vopen").href = `${url}#page=${page}`;
  document.querySelectorAll("a.cite.on").forEach(a => a.classList.remove("on"));
  if (link.classList.contains("cite")) link.classList.add("on");
  document.body.classList.add("viewing");
  vel("viewer").setAttribute("aria-hidden", "false");
  vel("vframe").hidden = true; vel("vpages").hidden = false;
  try {
    if (V.file !== file || !V.doc) {
      vel("vpages").innerHTML = `<div class="vmsg">Opening ${esc(shortName(file))}…</div>`;
      V.doc?.destroy(); V.doc = null; V.file = file; V.marks = null;
      const lib = await loadPdfjs();
      const doc = await lib.getDocument(url).promise;
      if (token !== V.token) return doc.destroy();
      V.doc = doc; V.n = doc.numPages; V.zoom = 1;
      await new Promise(r => setTimeout(r, 300));   // let the panel finish sliding open so the fit width is right
      await layout();
    }
    const target = Math.min(Math.max(page, 1), V.n);
    V.marks = null;
    setHeader(target, "finding the line…");
    const claim = claimFor(link);
    // A Python matrix lookup says exactly where its row and cell are: no guessing by words.
    const nums = (link.dataset.mark || "").split(/\s+/).filter(Boolean).map(Number);
    const placed = nums.length >= 4 && nums.length % 4 === 0;
    let at = target, rects = placed ? [...Array(nums.length / 4)].map((_, i) => nums.slice(i * 4, i * 4 + 4))
      : await findMarks(await V.doc.getPage(target), claim);
    // Citations are sometimes a page off. Look one page either side and say so in the header.
    for (const n of placed ? [] : [target + 1, target - 1]) {
      if (rects.length || n < 1 || n > V.n) continue;
      const r = await findMarks(await V.doc.getPage(n), claim);
      if (r.length) { at = n; rects = r; }
    }
    if (token !== V.token) return;
    V.marks = { page: at, rects };
    V.pinned = true;   // keep the marked line in view through refits until the reader scrolls
    await showMark();
    const marked = placed ? "matrix row and cell marked" : `${rects.length === 1 ? "line" : rects.length + " lines"} marked`;
    V.note = !rects.length ? `couldn't match the cited line on p. ${target}, check the page`
      : at !== target ? `closest match on p. ${at} (cited as p. ${target})` : marked;
    setHeader(at);
  } catch (e) {
    // A newer click took over (its document switch cancels this one's drawing): leave the panel to it.
    if (token !== V.token || e?.name === "RenderingCancelledException") return;
    // Fallback: the browser's own PDF viewer, no marks.
    console.warn("Guideline viewer fell back to the built-in PDF viewer:", e);
    V.doc = null; V.file = "";
    vel("vpages").hidden = true; vel("vframe").hidden = false;
    vel("vframe").src = `${url}?p=${page}#page=${page}&view=FitH&navpanes=0`;
    vel("vpage").textContent = `Page ${page} · ${file}`;
  }
}
async function showMark() {
  // Looked up fresh each time: a refit rebuilds the pages, so a page element held from before is detached.
  const d = vel("vpages").querySelector(`.vpg[data-n="${V.marks.page}"]`);
  delete d.dataset.drawn;   // redraw so the marks show
  await drawPage(V.marks.page);
  const first = d.querySelector(".vhl");
  vel("vpages").scrollTop = first ? d.offsetTop + first.offsetTop - vel("vpages").clientHeight / 3 : d.offsetTop - 12;
}
function closeViewer() {
  document.body.classList.remove("viewing");
  vel("viewer").setAttribute("aria-hidden", "true");
  document.querySelectorAll("a.cite.on").forEach(a => a.classList.remove("on"));
}
async function rezoom(factor) {
  if (!V.doc) return;
  const box = vel("vpages"), page = currentPage(), d = box.querySelector(`.vpg[data-n="${page}"]`);
  const within = d ? (box.scrollTop - d.offsetTop) / d.offsetHeight : 0;
  V.zoom = Math.min(3, Math.max(0.5, factor ? V.zoom * factor : 1));
  await layout();
  const nd = box.querySelector(`.vpg[data-n="${page}"]`);
  box.scrollTop = nd.offsetTop + within * nd.offsetHeight;
}
vel("vzoomin").addEventListener("click", () => rezoom(1.25));
vel("vzoomout").addEventListener("click", () => rezoom(0.8));
vel("vclose").addEventListener("click", closeViewer);
for (const ev of ["wheel", "touchmove", "keydown"]) vel("vpages").addEventListener(ev, () => { V.pinned = false; }, { passive: true });
vel("vpages").addEventListener("scroll", () => { if (V.doc) setHeader(currentPage()); }, { passive: true });
// Refit whenever the panel's width changes: window resizes, the chat width toggle, or a fit measured while the panel
// was still sliding open (that left pages drawn at a third of the panel's width).
let refit;
new ResizeObserver(() => {
  clearTimeout(refit);
  refit = setTimeout(() => {
    const w = vel("viewer").getBoundingClientRect().width;
    if (!V.doc || !w || Math.abs(w - (V.fitWidth || 0)) <= 8 || !document.body.classList.contains("viewing")) return;
    rezoom(V.zoom === 1 ? 0 : 1).then(() => V.marks && V.pinned && showMark()).catch(() => {});
  }, 200);
}).observe(vel("viewer"));
