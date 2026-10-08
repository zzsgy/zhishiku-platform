const $ = (id) => document.getElementById(id);
const config = JSON.parse($("readerConfig").textContent),
  root = $("universalReader"),
  isPdf = config.format === "PDF";
let viewer,
  eventBus,
  loadingTask,
  ready = false,
  interacted = false;
let page = config.page,
  offset = config.offset,
  percent = config.percent;
let saveTimer,
  revision = 0,
  savedRevision = 0,
  saving = false,
  matches = [],
  matchIndex = -1,
  searchTimer,
  headings = [],
  locationTouched = false;
const preferences = {
  fontSize: 18,
  lineHeight: 1.9,
  width: 820,
  paper: "auto",
  ...config.preferences,
};
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
function applyPreferences() {
  root.style.setProperty("--reader-font", preferences.fontSize + "px");
  root.style.setProperty("--reader-line", preferences.lineHeight);
  root.style.setProperty("--reader-width", preferences.width + "px");
  if (preferences.paper === "auto") delete root.dataset.paper;
  else root.dataset.paper = preferences.paper;
  $("readerFont").value = preferences.fontSize;
  $("readerFontValue").textContent = preferences.fontSize;
  $("readerLine").value = preferences.lineHeight;
  $("readerWidth").value = preferences.width;
  $("readerPaper").value = preferences.paper;
}
applyPreferences();
root.querySelector(".reader-text-controls").hidden = isPdf;
root.querySelector(".reader-pdf-settings").hidden = !isPdf;
root.querySelector(".reader-pdf-controls").hidden = !isPdf;
function fitPdf() {
  if (viewer && ready) viewer.currentScaleValue = $("readerZoom").value;
}
function togglePanel(kind, visible) {
  $(kind === "toc" ? "readerToc" : "kbSidePanel").hidden = !visible;
  $(kind === "toc" ? "readerTocToggle" : "readerNotesToggle").setAttribute(
    "aria-expanded",
    String(visible),
  );
  if (visible && innerWidth < 1200) {
    const other = kind === "toc" ? "notes" : "toc";
    $(other === "toc" ? "readerToc" : "kbSidePanel").hidden = true;
    $(other === "toc" ? "readerTocToggle" : "readerNotesToggle").setAttribute(
      "aria-expanded",
      "false",
    );
  }
  requestAnimationFrame(fitPdf);
}
$("readerTocToggle").onclick = () => togglePanel("toc", $("readerToc").hidden);
$("readerNotesToggle").onclick = () =>
  togglePanel("notes", $("kbSidePanel").hidden);
root.querySelectorAll("[data-close]").forEach(
  (button) =>
    (button.onclick = () => {
      togglePanel(button.dataset.close, false);
      $(
        button.dataset.close === "toc"
          ? "readerTocToggle"
          : "readerNotesToggle",
      ).focus();
    }),
);
if (innerWidth >= 1440) togglePanel("toc", true);
function setFocus(value) {
  document.body.classList.toggle("reader-focus", value);
  $("readerFocus").setAttribute("aria-pressed", String(value));
  $("readerFocus").setAttribute(
    "aria-label",
    value ? "退出专注模式" : "进入专注模式",
  );
  $("readerFocus").querySelector("span").textContent = value
    ? "退出专注"
    : "专注";
  requestAnimationFrame(fitPdf);
}
$("readerFocus").onclick = () =>
  setFocus(!document.body.classList.contains("reader-focus"));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    $("readerSettings").open = false;
    if (!$("readerSearchbar").hidden) closeSearch();
    else if (!$("kbSidePanel").hidden) togglePanel("notes", false);
    else if (!$("readerToc").hidden && innerWidth < 1200)
      togglePanel("toc", false);
    else if (document.body.classList.contains("reader-focus")) setFocus(false);
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
    event.preventDefault();
    openSearch();
  }
});
document.addEventListener("pointerdown", (event) => {
  if (!$("readerSettings").contains(event.target))
    $("readerSettings").open = false;
});
function locationLabel() {
  if (isPdf) return `第 ${page} 页`;
  let heading = "";
  for (const node of headings) {
    if (
      node.getBoundingClientRect().top <=
      $("textContainer").getBoundingClientRect().top + 100
    )
      heading = node.textContent;
  }
  return (heading || `阅读 ${Math.round(percent)}%`).slice(0, 64);
}
window.KB_READER_LOCATION = locationLabel;
function updateStatus() {
  $("readerPosition").textContent = isPdf
    ? `第 ${page} / ${viewer?.pagesCount || "—"} 页 · ${Math.round(percent)}%`
    : `已阅读 ${Math.round(percent)}%`;
  $("readerProgress").style.width = percent + "%";
  if (!locationTouched && document.activeElement !== $("readerNoteLocation"))
    $("readerNoteLocation").value = locationLabel();
}
function changed() {
  if (!ready || !interacted) return;
  revision++;
  clearTimeout(saveTimer);
  $("readerSaveStatus").textContent = "正在记住阅读位置…";
  saveTimer = setTimeout(() => savePosition(), 800);
}
async function savePosition(keepalive = false) {
  if (
    !ready ||
    !interacted ||
    savedRevision === revision ||
    (saving && !keepalive)
  )
    return;
  const currentRevision = revision;
  saving = true;
  try {
    const response = await fetch(config.positionUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        fileKey: config.fileKey,
        page,
        offset,
        percent,
        ...preferences,
      }),
      keepalive,
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "保存失败");
    savedRevision = currentRevision;
    if (revision === currentRevision) {
      $("readerSaveStatus").textContent = "阅读位置已保存";
      $("readerSaveStatus").removeAttribute("role");
      $("readerSaveStatus").removeAttribute("tabindex");
    }
  } catch (error) {
    $("readerSaveStatus").textContent = "位置未保存，点击重试";
    $("readerSaveStatus").title = error.message;
    $("readerSaveStatus").tabIndex = 0;
    $("readerSaveStatus").setAttribute("role", "button");
  } finally {
    saving = false;
    if (revision > currentRevision) savePosition();
  }
}
$("readerSaveStatus").onclick = () => savePosition();
$("readerSaveStatus").onkeydown = (event) => {
  if (event.key === "Enter") savePosition();
};
document.addEventListener("visibilitychange", () => {
  if (document.hidden) savePosition(true);
});
window.addEventListener("pagehide", () => savePosition(true));
["wheel", "touchmove", "pointerdown", "keydown"].forEach((type) =>
  root.addEventListener(
    type,
    () => {
      if (ready) interacted = true;
    },
    { capture: true, passive: true },
  ),
);
["readerFont", "readerLine", "readerWidth", "readerPaper"].forEach((id) =>
  $(id).addEventListener("input", () => {
    const fields = {
      readerFont: "fontSize",
      readerLine: "lineHeight",
      readerWidth: "width",
      readerPaper: "paper",
    };
    preferences[fields[id]] =
      id === "readerPaper" ? $(id).value : Number($(id).value);
    applyPreferences();
    changed();
  }),
);
function tocButton(text, level, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = text;
  button.dataset.level = level;
  button.onclick = () => {
    $("readerTocList")
      .querySelectorAll(".active")
      .forEach((node) => node.classList.remove("active"));
    button.classList.add("active");
    interacted = true;
    action();
    if (innerWidth < 1200) togglePanel("toc", false);
  };
  $("readerTocList").append(button);
}
function notice(text) {
  $("readerNotice").textContent = text;
  $("readerNotice").hidden = !text;
}
function fail(message) {
  ready = false;
  $("readerLoading").hidden = true;
  $("pdfContainer").hidden = true;
  $("textContainer").hidden = true;
  $("readerErrorText").textContent = message;
  $("readerError").hidden = false;
  $("readerTocList").textContent = "正文暂不可用，原件仍可下载。";
}
$("readerReload").onclick = () => location.reload();
async function openPdf() {
  const vendor = "/static/vendor/pdfjs-6.4.299/",
    pdfjs = await import(vendor + "build/pdf.mjs"),
    components = await import(vendor + "web/pdf_viewer.mjs");
  pdfjs.GlobalWorkerOptions.workerSrc = vendor + "build/pdf.worker.mjs";
  eventBus = new components.EventBus();
  const linkService = new components.PDFLinkService({
      eventBus,
      externalLinkTarget: components.LinkTarget.BLANK,
      externalLinkRel: "noopener noreferrer",
    }),
    findController = new components.PDFFindController({
      eventBus,
      linkService,
    });
  $("pdfContainer").hidden = false;
  viewer = new components.PDFViewer({
    container: $("pdfContainer"),
    viewer: $("pdfViewer"),
    eventBus,
    linkService,
    findController,
    downloadManager: new components.DownloadManager(),
    annotationMode: pdfjs.AnnotationMode.ENABLE,
    enableAutoLinking: false,
    enablePermissions: true,
    imageResourcesPath: vendor + "web/images/",
    maxCanvasPixels: 12000000,
    l10n: new components.GenericL10n("zh-CN"),
  });
  linkService.setViewer(viewer);
  eventBus.on("pagesinit", () => {
    viewer.currentScaleValue = $("readerZoom").value;
    page = clamp(config.page || 1, 1, viewer.pagesCount);
    $("readerPageTotal").textContent = viewer.pagesCount;
    $("readerPage").max = viewer.pagesCount;
    viewer.scrollPageIntoView({ pageNumber: page });
    if (config.offset) {
      const div = viewer.getPageView(page - 1).div;
      $("pdfContainer").scrollTop =
        div.offsetTop + div.offsetHeight * config.offset;
    }
    $("readerLoading").hidden = true;
    ready = true;
    root.dataset.ready = "true";
    updateStatus();
  });
  eventBus.on("pagechanging", (event) => {
    page = event.pageNumber;
    $("readerPage").value = page;
    $("readerPrev").disabled = page <= 1;
    $("readerNext").disabled = page >= viewer.pagesCount;
  });
  eventBus.on("updateviewarea", () => {
    if (!viewer.pagesCount) return;
    page = viewer.currentPageNumber;
    const div = viewer.getPageView(page - 1).div;
    offset = clamp(
      ($("pdfContainer").scrollTop - div.offsetTop) / div.offsetHeight,
      0,
      1,
    );
    percent = ((page - 1 + offset) / viewer.pagesCount) * 100;
    if (
      page === viewer.pagesCount &&
      $("pdfContainer").scrollTop + $("pdfContainer").clientHeight >=
        $("pdfContainer").scrollHeight - 8
    )
      percent = 100;
    updateStatus();
    changed();
  });
  eventBus.on("updatefindmatchescount", (event) => {
    const count = event.matchesCount;
    $("readerSearchCount").textContent = count.total
      ? `${count.current} / ${count.total}`
      : "没有匹配";
  });
  eventBus.on("updatefindcontrolstate", (event) => {
    if (event.state === components.FindState.PENDING)
      $("readerSearchCount").textContent = "搜索中…";
    if (event.state === components.FindState.NOT_FOUND)
      $("readerSearchCount").textContent = "没有匹配（扫描件需 OCR）";
  });
  loadingTask = pdfjs.getDocument({
    url: config.fileUrl,
    cMapUrl: vendor + "cmaps/",
    cMapPacked: true,
    standardFontDataUrl: vendor + "standard_fonts/",
    wasmUrl: vendor + "wasm/",
    iccUrl: vendor + "iccs/",
    isEvalSupported: false,
    enableXfa: false,
    withCredentials: true,
  });
  loadingTask.onPassword = (submit, reason) => {
    const dialog = $("readerPasswordDialog");
    $("readerPasswordError").textContent =
      reason === pdfjs.PasswordResponses.INCORRECT_PASSWORD
        ? "密码不正确，请重试。"
        : "";
    $("readerPassword").value = "";
    dialog.onclose = () => {
      if (dialog.returnValue === "open") submit($("readerPassword").value);
      else {
        loadingTask.destroy();
        fail("未输入文档密码，您仍可下载原件。");
      }
    };
    dialog.returnValue = "cancel";
    dialog.showModal();
    $("readerPassword").focus();
  };
  const document = await loadingTask.promise;
  if (document.numPages > 10000)
    throw new Error("PDF 超过 10000 页，请下载原件分段阅读。");
  viewer.setDocument(document);
  linkService.setDocument(document);
  $("readerTocList").replaceChildren();
  const outline = await document.getOutline();
  function buildOutline(items, level = 1) {
    for (const item of items) {
      if (item.dest)
        tocButton(item.title || "未命名章节", Math.min(level, 3), () =>
          linkService.goToDestination(item.dest),
        );
      if (item.items?.length) buildOutline(item.items, level + 1);
    }
  }
  if (outline?.length) buildOutline(outline);
  if (!$("readerTocList").children.length) {
    $("readerTocTitle").textContent = "页面导航";
    for (let index = 1; index <= document.numPages; index++)
      tocButton(`第 ${index} 页`, 1, () =>
        viewer.scrollPageIntoView({ pageNumber: index }),
      );
  }
  $("readerPrev").onclick = () => {
    interacted = true;
    viewer.currentPageNumber--;
  };
  $("readerNext").onclick = () => {
    interacted = true;
    viewer.currentPageNumber++;
  };
  $("readerPage").onchange = () => {
    interacted = true;
    viewer.currentPageNumber = clamp(
      Number($("readerPage").value) || 1,
      1,
      viewer.pagesCount,
    );
    $("readerPage").value = viewer.currentPageNumber;
  };
  $("readerZoom").onchange = () => {
    viewer.currentScaleValue = $("readerZoom").value;
  };
  new ResizeObserver(() => {
    if (ready && ["page-width", "page-fit"].includes($("readerZoom").value))
      fitPdf();
  }).observe($("pdfContainer"));
}
async function openText() {
  const response = await fetch(config.previewUrl),
    data = await response.json();
  if (!response.ok || !data.ok) throw new Error(data.error || "文档预览失败。");
  $("readerBody").innerHTML = data.html; // Server emits escaped, allowlisted document structure.
  // Isolate document anchors from application controls while preserving internal links.
  const anchors = new Map();
  $("readerBody")
    .querySelectorAll("[id]")
    .forEach((node) => {
      const previous = node.id;
      node.id = `document-${config.book}-${previous}`;
      if (!anchors.has(previous)) anchors.set(previous, node.id);
    });
  $("readerBody")
    .querySelectorAll('a[href^="#"]')
    .forEach((link) => {
      try {
        const target = decodeURIComponent(link.getAttribute("href").slice(1));
        if (anchors.has(target))
          link.setAttribute(
            "href",
            "#" + encodeURIComponent(anchors.get(target)),
          );
      } catch {}
    });
  notice(data.warnings.join("；"));
  $("textContainer").hidden = false;
  $("readerTocList").replaceChildren();
  headings = Array.from($("readerBody").querySelectorAll("h1,h2,h3,h4,h5,h6"));
  headings.forEach((node, index) => {
    if (!node.id) node.id = "reader-heading-" + index;
    tocButton(
      node.textContent,
      Math.min(Number(node.tagName.slice(1)), 3),
      () => node.scrollIntoView({ block: "start" }),
    );
  });
  if (!headings.length)
    tocButton("文档开始", 1, () => {
      $("textContainer").scrollTop = 0;
    });
  const images = Array.from($("readerBody").querySelectorAll("img"));
  for (const image of images)
    image.addEventListener(
      "error",
      () => {
        const hint = document.createElement("p");
        hint.className = "reader-fidelity-note";
        hint.textContent = "此图片未能预览，请对照原件。";
        image.replaceWith(hint);
      },
      { once: true },
    );
  // Lazy Word images reserve their original aspect ratio. A decode rejection before
  // intersection is not a failed image request and must not remove the illustration.
  await Promise.all(
    images
      .filter((image) => image.loading !== "lazy")
      .map((image) => image.decode().catch(() => {})),
  );
  await document.fonts.ready;
  const scroll = $("textContainer");
  scroll.scrollTop =
    config.offset * Math.max(0, scroll.scrollHeight - scroll.clientHeight);
  $("readerLoading").hidden = true;
  ready = true;
  root.dataset.ready = "true";
  const onScroll = () => {
    const range = scroll.scrollHeight - scroll.clientHeight;
    offset = range > 0 ? clamp(scroll.scrollTop / range, 0, 1) : 0;
    percent = offset * 100;
    page = 1;
    updateStatus();
    changed();
  };
  scroll.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
}
function openSearch() {
  $("readerSearchbar").hidden = false;
  $("readerSearchToggle").setAttribute("aria-expanded", "true");
  $("readerSearch").focus();
}
function closeSearch() {
  $("readerSearchbar").hidden = true;
  $("readerSearchToggle").setAttribute("aria-expanded", "false");
  $("readerSearch").value = "";
  if (eventBus) eventBus.dispatch("findbarclose", { source: root });
  else clearMatches();
  $("readerSearchToggle").focus();
}
$("readerSearchToggle").onclick = () =>
  $("readerSearchbar").hidden ? openSearch() : closeSearch();
$("readerSearchClose").onclick = closeSearch;
function clearMatches() {
  $("readerBody")
    .querySelectorAll("mark.reader-match")
    .forEach((node) =>
      node.replaceWith(document.createTextNode(node.textContent)),
    );
  $("readerBody").normalize();
  matches = [];
  matchIndex = -1;
}
function textSearch(query) {
  clearMatches();
  if (!query) {
    $("readerSearchCount").textContent = "";
    return;
  }
  const walker = document.createTreeWalker(
      $("readerBody"),
      NodeFilter.SHOW_TEXT,
    ),
    nodes = [];
  let text = "",
    previousBlock = null;
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const block = node.parentElement.closest(
      "p,h1,h2,h3,h4,h5,h6,li,td,th,pre,blockquote",
    );
    if (block !== previousBlock && text) text += "\n";
    nodes.push({
      node,
      start: text.length,
      end: text.length + node.textContent.length,
    });
    text += node.textContent;
    previousBlock = block;
  }
  const lower = text.toLocaleLowerCase(),
    term = query.toLocaleLowerCase(),
    ranges = [];
  let index = lower.indexOf(term);
  while (index >= 0 && ranges.length < 400) {
    ranges.push({ start: index, end: index + query.length, marks: [] });
    index = lower.indexOf(term, index + query.length);
  }
  // Search across adjacent styled runs while retaining every original HTML node.
  for (const entry of nodes) {
    const { node } = entry;
    const overlaps = ranges.filter(
      (range) => range.end > entry.start && range.start < entry.end,
    );
    if (!overlaps.length) continue;
    let start = 0;
    const fragment = document.createDocumentFragment();
    for (const range of overlaps) {
      const first = Math.max(0, range.start - entry.start),
        last = Math.min(node.textContent.length, range.end - entry.start);
      fragment.append(
        document.createTextNode(node.textContent.slice(start, first)),
      );
      const mark = document.createElement("mark");
      mark.className = "reader-match";
      mark.textContent = node.textContent.slice(first, last);
      fragment.append(mark);
      range.marks.push(mark);
      start = last;
    }
    fragment.append(document.createTextNode(node.textContent.slice(start)));
    node.replaceWith(fragment);
  }
  matches = ranges.map((range) => range.marks).filter((group) => group.length);
  moveMatch(false);
}
function moveMatch(previous) {
  if (!matches.length) {
    $("readerSearchCount").textContent = $("readerSearch").value
      ? "没有匹配"
      : "";
    return;
  }
  if (matchIndex >= 0)
    matches[matchIndex].forEach((node) => node.classList.remove("current"));
  matchIndex =
    (matchIndex + (previous ? -1 : 1) + matches.length) % matches.length;
  matches[matchIndex].forEach((node) => node.classList.add("current"));
  matches[matchIndex][0].scrollIntoView({ block: "center" });
  $("readerSearchCount").textContent =
    `${matchIndex + 1} / ${matches.length}${matches.length === 400 ? "（前400项）" : ""}`;
}
function search(previous = false, again = false) {
  const query = $("readerSearch").value.trim();
  if (eventBus)
    eventBus.dispatch("find", {
      source: root,
      type: again ? "again" : "",
      query,
      caseSensitive: false,
      entireWord: false,
      highlightAll: true,
      findPrevious: previous,
      matchDiacritics: false,
    });
  else if (again) moveMatch(previous);
  else textSearch(query);
}
$("readerSearch").oninput = () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => search(), 250);
};
$("readerSearchbar").onsubmit = (event) => {
  event.preventDefault();
  interacted = true;
  search(false, true);
};
$("readerSearchPrev").onclick = () => search(true, true);
$("readerNoteLocation").oninput = () => {
  locationTouched = true;
};
$("readerNoteForm").onsubmit = async (event) => {
  event.preventDefault();
  const button = event.target.querySelector("button");
  button.disabled = true;
  try {
    const note = $("readerNoteText").value.trim();
    if (!note) return;
    const location = $("readerNoteLocation").value.slice(0, 64);
    const response = await fetch("/bookshelf/api/note/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book: config.book, note, location }),
      }),
      data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "保存失败");
    $("kbNoteList").querySelector(".kb-note-empty")?.remove();
    const item = document.createElement("li"),
      label = document.createElement("div"),
      row = document.createElement("div"),
      content = document.createElement("div"),
      remove = document.createElement("button");
    label.className = "reader-note-location";
    label.textContent = location || "阅读笔记";
    row.className = "d-flex gap-2";
    content.className = "flex-fill reader-note-content";
    content.textContent = note;
    remove.className = "btn btn-sm btn-link text-danger p-0 kb-del";
    remove.dataset.type = "note";
    remove.dataset.id = data.id;
    remove.setAttribute("aria-label", "删除笔记");
    remove.innerHTML = '<i class="bi bi-trash"></i>';
    row.append(content, remove);
    item.append(label, row);
    $("kbNoteList").prepend(item);
    $("kbNoteCount").textContent = Number($("kbNoteCount").textContent) + 1;
    $("readerNoteText").value = "";
    $("readerNoteStatus").textContent = "笔记已保存";
    locationTouched = false;
  } catch (error) {
    $("readerNoteStatus").textContent = error.message;
  } finally {
    button.disabled = false;
  }
};
new MutationObserver(() => {
  const count = $("kbNoteCount").textContent;
  root.querySelector(".reader-count").textContent = count;
}).observe($("kbNoteCount"), { childList: true });
try {
  if (!config.available) throw new Error("原件暂不可用，请核对文件目录。");
  await (isPdf ? openPdf() : openText());
} catch (error) {
  fail(error.message || "无法打开文档，请下载原件核对。");
}
