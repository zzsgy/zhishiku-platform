/* 知识编辑器内核：所见即所得（WYSIWYG）+ Markdown 双模式
 * ============================================================================
 * 设计前提
 *   1. 正文的持久化格式始终是 Markdown（content_md），Markdown 是"存储格式"，
 *      不是"内容本身"——所以编辑态里绝不该出现 ** ## - > ` 这些标记。
 *   2. 编辑态呈现的 HTML 由服务端 render_markdown() 生成，与阅读页同一个渲染器。
 *      「编辑态所见 == 保存后阅读页所见」，两端不存在第二套渲染规则。
 *   3. 编辑器只暴露渲染器真正支持的格式（例如本渲染器不支持删除线，
 *      编辑器就不提供删除线按钮，避免"编辑时有、存完就没有"的错觉）。
 *
 * 数据流
 *   content_md ──服务端 render_markdown──▶ 富文本编辑区(DOM)
 *   富文本编辑区(DOM) ──本文件 serialize()──▶ 隐藏 textarea[name=content_md]
 *   textarea ──(可选) 源码模式──▶ 用户直接改 Markdown
 *   表单提交后服务端再做一次「内容骤减」熔断，兜住往返异常。
 *
 * 防损策略（三层，缺一不可）
 *   ① 未建模的属性一律以原始 HTML 原样透传（outerHTML），不猜、不丢；
 *   ② 每次同步把 Markdown 存一份到 localStorage（可手动恢复）；
 *   ③ 保存前本地比对信息量，骤减需二次确认；服务端再独立熔断一次。
 * ============================================================================
 */
(function () {
  'use strict';

  var rich = document.getElementById('mdRich');
  var src = document.getElementById('mdInput');
  var form = document.getElementById('mdForm');
  var bar = document.getElementById('mdToolbar');
  var tblBar = document.getElementById('mdTableBar');
  var countEl = document.getElementById('mdCount');
  var modeBtn = document.getElementById('mdModeBtn');
  var forceEl = document.getElementById('forceSave');
  var warnEl = document.getElementById('mdDraftBar');
  if (!rich || !src || !form) return;

  var renderUrl = rich.getAttribute('data-render-url') || '/wiki/api/render/';
  var draftKey = 'kbmd:' + (rich.getAttribute('data-node-id') || 'new');
  var initialMd = src.value || '';
  var richMode = true;

  /* ------------------------------------------------------------------ 工具 */

  function escHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* 工具栏"轻提示"：解释"这个操作在当前位置为什么没有意义"。
     静默失败会让人以为按钮坏了；一句人话比什么都不说更接近人的预期。 */
  var hintEl = null;
  var hintTimer = null;
  function flashHint(msg) {
    if (!hintEl) {
      hintEl = document.createElement('div');
      hintEl.className = 'kb-hint-toast';
      document.body.appendChild(hintEl);
    }
    hintEl.textContent = msg;
    hintEl.classList.add('show');
    clearTimeout(hintTimer);
    hintTimer = setTimeout(function () { hintEl.classList.remove('show'); }, 2600);
  }

  /* 文本 → Markdown 文本：转义可能被误解析的字符。
     转义是"准确性"的基石：`2L_历史数据` 里的下划线、`5*3` 里的星号都不会
     被解析成强调；反斜杠转义后渲染结果与原文完全一致（阅读页看到的还是 _ 和 *）。

     注意：不要在这里把段落内的换行折叠成空格 —— python-markdown 会原样保留段内的
     软换行（渲染成 HTML 后只是一个空格，但源码里确实是一行），折叠掉就等于悄悄把
     两行并成一行。真正需要丢弃的只有 <br> 之后那个排版用换行，见 inlineMd 的 afterBr。 */
  function escMd(text, inTable) {
    var t = String(text == null ? '' : text).replace(/\r\n?/g, '\n');
    t = t.replace(/\\/g, '\\\\');
    t = t.replace(/([*_`\[\]])/g, '\\$1');
    if (inTable) t = t.replace(/\|/g, '\\|');
    t = t.replace(/^(\s*)([#>])/gm, '$1\\$2');
    t = t.replace(/^(\s*)([-+])(\s)/gm, '$1\\$2$3');
    t = t.replace(/^(\s*)(\d+)\.(\s)/gm, '$1$2\\.$3');
    return t;
  }

  /* 取节点文本，把 <br> 当换行（<pre> 内的软换行就是靠 <br> 承载的） */
  function textWithBr(el) {
    var out = '';
    (function walk(n) {
      for (var i = 0; i < n.childNodes.length; i++) {
        var k = n.childNodes[i];
        if (k.nodeType === 3) out += k.nodeValue;
        else if (k.nodeType === 1) {
          if (k.tagName === 'BR') out += '\n';
          else if (k.tagName === 'IMG') out += k.getAttribute('alt') || '';
          else walk(k);
        }
      }
    })(el);
    return out;
  }

  /* 行内标记：把 DOM 的加粗/斜体还原成 Markdown 标记。
     首尾空白必须留到标记外，否则 `** 粗 **` 在 Markdown 里不是强调。 */
  function wrapMd(marks, inner) {
    if (!marks || !marks.length) return inner;
    var lead = (inner.match(/^\s*/) || [''])[0];
    var trail = (inner.match(/\s*$/) || [''])[0];
    var core = inner.slice(lead.length, inner.length - trail.length);
    if (!core) return inner;
    var open = '', close = '';
    for (var i = 0; i < marks.length; i++) { open += marks[i]; close = marks[i] + close; }
    return lead + open + core + close + trail;
  }

  function codeMd(t) {
    t = String(t == null ? '' : t);
    if (t.indexOf('`') === -1) return '`' + t + '`';
    var ticks = t.indexOf('``') === -1 ? '``' : '```';
    return ticks + ' ' + t + ' ' + ticks;
  }

  /* 未知属性 = 我不理解的语义 → 原样透传原始 HTML，宁可不加工也不丢 */
  var KNOWN_ATTRS = {
    A: { href: 1, class: 1, title: 1 },
    IMG: { src: 1, alt: 1, class: 1, title: 1 },
    CODE: { class: 1 },
    SPAN: { style: 1, class: 1 },
    DIV: { class: 1 },
    TD: { style: 1, align: 1, colspan: 1, rowspan: 1 },
    TH: { style: 1, align: 1, colspan: 1, rowspan: 1 },
    OL: { start: 1, class: 1 },
    UL: { class: 1 }
  };

  function hasUnknownAttr(el) {
    if (!el.attributes || !el.attributes.length) return false;
    var allow = KNOWN_ATTRS[el.tagName] || {};
    for (var i = 0; i < el.attributes.length; i++) {
      var name = el.attributes[i].name.toLowerCase();
      if (name === 'contenteditable') continue;
      if (!allow[name]) return true;
    }
    return false;
  }

  function styleMarks(el) {
    var st = (el.getAttribute('style') || '').toLowerCase();
    var marks = [];
    if (/font-weight\s*:\s*(bold|[6-9]00)/.test(st)) marks.push('**');
    if (/font-style\s*:\s*italic/.test(st)) marks.push('*');
    // 下划线 / 删除线 / 颜色在 Markdown 里没有对应语义：不产出标记，
    // 与阅读页的表现保持一致（阅读页同样只会显示纯文本）。
    return marks;
  }

  /* ------------------------------------------------------ DOM → Markdown */

  var INLINE_MARKS = { STRONG: ['**'], B: ['**'], EM: ['*'], I: ['*'] };

  function linkMd(a) {
    var href = a.getAttribute('href') || '';
    if (a.className && a.className.indexOf('kb-wikilink') !== -1) {
      var title = '';
      var m = /[?&]title=([^&]*)/.exec(href);
      if (m) { try { title = decodeURIComponent(m[1]); } catch (e) { title = m[1]; } }
      var label = (a.textContent || '').replace(/^\s*\[\[/, '').replace(/\]\]\s*$/, '').trim();
      title = title || label;
      return '[[' + title + (label && label !== title ? '|' + label : '') + ']]';
    }
    return '[' + inlineMd(a, {}) + '](' + href + ')';
  }

  var UNWRAP_OK = { SPAN: 1, FONT: 1, SMALL: 1, U: 1, STRIKE: 1, S: 1, DEL: 1,
    MARK: 1, TIME: 1, SUB: 1, SUP: 1, LABEL: 1 };

  /* 单个节点的行内序列化。返回 {md, br}：br 表示"此处刚产出硬换行"，
     调用方据此抹掉紧随其后的排版空白。
     必须按"节点自身"分派（而不是一律取子节点），否则 <code>/<a>/<strong>
     作为列表项、表格格等容器的直接子节点时，会只剩下纯文本、丢掉标记。 */
  function inlineNode(n, opts, afterBr) {
    if (n.nodeType === 3) {
      var chunk = escMd(n.nodeValue, opts.table);
      // <br> 之后紧跟的空白/换行是渲染器格式化产物，必须丢掉，
      // 否则会和 <br> 自己产出的两个空格拼成空行 → 一句话被拆成两段
      if (afterBr) chunk = chunk.replace(/^[ \t\n]+/, '');
      return { md: chunk, br: false };
    }
    if (n.nodeType !== 1) return { md: '', br: false };
    var tag = n.tagName;
    if (tag === 'BR') return { md: opts.table ? '<br>' : '  \n', br: true };
    if (tag === 'IMG') {
      return {
        md: '![' + escMd(n.getAttribute('alt') || '') + '](' + (n.getAttribute('src') || '') + ')',
        br: false
      };
    }
    if (tag === 'A') return { md: linkMd(n), br: false };
    if (tag === 'CODE') return { md: codeMd(n.textContent), br: false };
    if (INLINE_MARKS[tag]) return { md: wrapMd(INLINE_MARKS[tag], inlineChildren(n, opts)), br: false };
    if (hasUnknownAttr(n)) return { md: n.outerHTML, br: false };   // 透传
    if (UNWRAP_OK[tag]) {
      var inner = inlineChildren(n, opts);
      var marks = tag === 'SPAN' ? styleMarks(n) : [];
      return { md: marks.length ? wrapMd(marks, inner) : inner, br: false };
    }
    return { md: inlineChildren(n, opts), br: false };
  }

  function inlineChildren(node, opts) {
    var out = '';
    var afterBr = false;
    var kids = node.childNodes;
    for (var i = 0; i < kids.length; i++) {
      var r = inlineNode(kids[i], opts, afterBr);
      out += r.md;
      afterBr = r.br;
    }
    return out;
  }

  /* 对"容器"取行内内容（p / h / li / td / a 等） */
  function inlineMd(node, opts) { return inlineChildren(node, opts); }

  var BLOCK_TAGS = {
    H1: 1, H2: 1, H3: 1, H4: 1, H5: 1, H6: 1, P: 1, UL: 1, OL: 1, BLOCKQUOTE: 1,
    PRE: 1, HR: 1, TABLE: 1, DIV: 1, FIGURE: 1, DL: 1, ADDRESS: 1, SECTION: 1,
    ARTICLE: 1, HEADER: 1, FOOTER: 1, MAIN: 1, FORM: 1, DETAILS: 1, SUMMARY: 1
  };

  function listMd(list, depth) {
    var ordered = list.tagName === 'OL';
    var start = parseInt(list.getAttribute('start') || '1', 10) || 1;
    var lines = [];
    var idx = 0;
    for (var i = 0; i < list.children.length; i++) {
      var li = list.children[i];
      if (li.tagName !== 'LI') continue;
      var marker = ordered ? ((start + idx) + '. ') : '- ';
      idx++;
      var head = '', nested = [], kids = li.childNodes;
      for (var j = 0; j < kids.length; j++) {
        var k = kids[j];
        if (k.nodeType === 1 && (k.tagName === 'UL' || k.tagName === 'OL')) { nested.push(k); continue; }
        if (k.nodeType === 3) { head += escMd(k.nodeValue); continue; }
        if (k.nodeType !== 1) continue;
        if (k.tagName === 'P' || k.tagName === 'DIV') {
          var sub = blockMd(k, depth);
          if (sub) head += (head ? ' ' : '') + sub.replace(/\n+/g, ' ');
          continue;
        }
        head += inlineNode(k, {}, false).md;
      }
      head = head.replace(/\s+/g, ' ').trim();
      lines.push(' '.repeat(depth * 4) + marker + head);
      for (var m = 0; m < nested.length; m++) lines.push(listMd(nested[m], depth + 1));
    }
    return lines.join('\n');
  }

  function preMd(el) {
    var code = null;
    for (var i = 0; i < el.children.length; i++) {
      if (el.children[i].tagName === 'CODE') { code = el.children[i]; break; }
    }
    var text = textWithBr(code || el).replace(/\n+$/, '');
    var lang = '';
    if (code) {
      var m = /language-([\w+#.-]+)/.exec(code.className || '');
      if (m) lang = m[1];
    }
    var fence = '```';
    while (text.indexOf(fence) !== -1) fence += '`';
    return fence + lang + '\n' + text + '\n' + fence;
  }

  function alignOf(cell, colIdx) {
    var st = (cell.getAttribute('style') || '').toLowerCase();
    var m = /text-align\s*:\s*(left|center|right)/.exec(st);
    var v = m ? m[1] : (cell.getAttribute('align') || '').toLowerCase();
    return v === 'left' || v === 'center' || v === 'right' ? v : '';
  }

  function tableMd(table) {
    var trs = table.querySelectorAll('tr');
    if (!trs.length) return '';
    var header = null, body = [], aligns = [];
    for (var i = 0; i < trs.length; i++) {
      var cells = trs[i].children, row = [];
      for (var j = 0; j < cells.length; j++) {
        var c = cells[j];
        var txt = inlineMd(c, { table: true }).replace(/\s*\n\s*/g, '<br>').trim();
        row.push(txt);
        if (i === 0) aligns.push(alignOf(c, j));
      }
      var isHead = cells.length && (cells[0].tagName === 'TH');
      if (i === 0 && isHead) header = row; else body.push(row);
    }
    if (!header) { header = body.shift() || []; }
    var cols = header.length;
    for (var b = 0; b < body.length; b++) cols = Math.max(cols, body[b].length);
    if (!cols) return '';
    function pad(row) {
      var r = row.slice();
      while (r.length < cols) r.push('');
      return r;
    }
    function line(row) { return '| ' + pad(row).join(' | ') + ' |'; }
    var sep = [];
    for (var c = 0; c < cols; c++) {
      var a = aligns[c] || '';
      sep.push(a === 'center' ? ':--:' : (a === 'right' ? '--:' : (a === 'left' ? ':--' : '---')));
    }
    var rows = [line(header), '| ' + sep.join(' | ') + ' |'];
    for (var r2 = 0; r2 < body.length; r2++) rows.push(line(body[r2]));
    return rows.join('\n');
  }

  function quoteMd(el) {
    var inner = serialize(el);
    return inner.replace(/\n+$/, '').split('\n').map(function (l) {
      return l.trim() ? '> ' + l : '>';
    }).join('\n');
  }

  /* 返回该元素的 Markdown 块；返回 null 表示"行内级"，交给段落缓冲 */
  function blockMd(el, depth) {
    var tag = el.tagName;
    if (/^H[1-6]$/.test(tag)) {
      var lv = parseInt(tag.slice(1), 10);
      // 标题 id 由渲染器的 toc 扩展生成；用 attr_list 语法原样写回，
      // 保存后重新渲染仍得到同样的 id（锚点稳定，往返零变化）。
      return '#'.repeat(lv) + ' ' + inlineMd(el, {}).trim() +
        (el.id ? ' {#' + el.id + '}' : '');
    }
    if (tag === 'P') {
      // Chrome 的 insertUnorderedList / insertHorizontalRule 有时会把块级元素
      // 塞进 <p> 里（非法嵌套，如 <p><ul><li>…</li></ul></p>）。若仍按"纯段落"
      // 取行内内容，列表标记/表格就被吃掉了 —— 这里必须把内嵌块拆出来单独序列化。
      var hasBlockChild = false;
      for (var pk = 0; pk < el.children.length; pk++) {
        if (BLOCK_TAGS[el.children[pk].tagName]) { hasBlockChild = true; break; }
      }
      if (hasBlockChild) {
        var parts = [];
        var run = '';
        for (var ci = 0; ci < el.childNodes.length; ci++) {
          var cn = el.childNodes[ci];
          if (cn.nodeType === 1 && BLOCK_TAGS[cn.tagName]) {
            var preTxt = run.trim();
            if (preTxt) parts.push(preTxt);
            run = '';
            var blk = blockMd(cn, depth);
            if (blk) parts.push(blk);
          } else {
            run += inlineNode(cn, {}, false).md;
          }
        }
        var postTxt = run.trim();
        if (postTxt) parts.push(postTxt);
        return parts.join('\n\n');
      }
      var t = inlineMd(el, {});
      return t.trim() ? t : '';
    }
    if (tag === 'UL' || tag === 'OL') return listMd(el, depth || 0);
    if (tag === 'BLOCKQUOTE') return quoteMd(el);
    if (tag === 'PRE') return preMd(el);
    if (tag === 'HR') return '---';
    if (tag === 'TABLE') return tableMd(el);
    if (tag === 'DIV' || tag === 'SECTION' || tag === 'ARTICLE' || tag === 'HEADER' ||
        tag === 'FOOTER' || tag === 'MAIN' || tag === 'FIGURE' || tag === 'DETAILS') {
      if (el.className && el.className.indexOf('kb-table-wrap') !== -1) {
        var tb = el.querySelector('table');
        return tb ? tableMd(tb) : '';
      }
      if (hasUnknownAttr(el)) return el.outerHTML;    // 透传原始 HTML，绝不猜
      var sub = serialize(el);
      return sub.replace(/\s+$/, '');
    }
    if (tag === 'DL') {
      var out = [];
      for (var i = 0; i < el.children.length; i++) {
        var c = el.children[i];
        if (c.tagName === 'DT') out.push('**' + inlineMd(c, {}).trim() + '**');
        else if (c.tagName === 'DD') out.push(': ' + inlineMd(c, {}).trim());
      }
      return out.join('\n');
    }
    if (BLOCK_TAGS[tag]) {
      var s = serialize(el);
      return s.replace(/\s+$/, '');
    }
    return null;
  }

  /* 把编辑区序列化成 Markdown。裸文本（浏览器可能直接放在 contenteditable 根下）
     与相邻行内元素合并成一个段落，不产生多余空行。 */
  function serialize(root) {
    var blocks = [];
    var buf = '';
    function flush() {
      var s = buf.replace(/\n{3,}/g, '\n\n').trim();
      if (s) blocks.push(s);
      buf = '';
    }
    var kids = root.childNodes;
    for (var i = 0; i < kids.length; i++) {
      var n = kids[i];
      if (n.nodeType === 3) {
        if (n.nodeValue && n.nodeValue.replace(/\s+/g, '')) buf += escMd(n.nodeValue);
        continue;
      }
      if (n.nodeType !== 1) continue;
      if (!BLOCK_TAGS[n.tagName]) { buf += inlineNode(n, {}, false).md; continue; }
      flush();
      var b = blockMd(n, 0);
      if (b) blocks.push(b);
    }
    flush();
    return blocks.join('\n\n').replace(/\n{3,}/g, '\n\n').replace(/\s+$/, '') + '\n';
  }

  /* ------------------------------------------------------ 双向同步 / 统计 */

  function weight(md) {
    return (md || '').replace(/[#>*`_~\[\]()!|\s]/g, '').length;
  }

  function syncFromRich() {
    if (!richMode) return;
    var md = serialize(rich);
    src.value = md;
    updateCount(md);
    markEmpty(md);
    saveDraft();
  }
  var syncTimer = null;
  function queueSync() {
    clearTimeout(syncTimer);
    syncTimer = setTimeout(syncFromRich, 160);
  }

  /* 空文档才显示占位提示，有内容就撤掉（避免提示和正文叠在一起） */
  function markEmpty(md) {
    rich.classList.toggle('kb-editor-empty', !String(md == null ? src.value : md).trim());
  }

  function updateCount(md) {
    if (!countEl) return;
    var w = weight(md == null ? (richMode ? serialize(rich) : src.value) : md);
    countEl.textContent = w + ' 字 · ' + (richMode ? '所见即所得' : 'Markdown 源码');
  }

  function saveDraft() {
    try {
      localStorage.setItem(draftKey, JSON.stringify({ md: src.value, at: Date.now() }));
    } catch (e) { /* 隐私模式/配额满：草稿是加分项，失败不影响编辑 */ }
  }

  /* ---------------------------------------------------------- 富文本注入 */

  function injectRich(html) {
    rich.innerHTML = html || '';
    if (!rich.innerHTML.trim()) rich.innerHTML = '<p><br></p>';
    markEmpty();
  }

  /* 链接在编辑区里不可跳转：点一下就离开编辑页会丢未保存内容。
     需要打开时按住 Ctrl/Cmd 点击。 */
  rich.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a');
    if (a && !(e.ctrlKey || e.metaKey)) e.preventDefault();
  });
  rich.addEventListener('input', queueSync);
  rich.addEventListener('blur', function () { rememberRange(); syncFromRich(); });
  ['keyup', 'mouseup'].forEach(function (ev) {
    rich.addEventListener(ev, function () { rememberRange(); syncToolbarState(); });
  });

  /* ------------------------------------------------------------ 编辑命令 */

  /* 编辑区里"最后一次有效选区"。原生的段落下拉框一点开，焦点就离开编辑区；
     而「把光标所在段落改成标题」这类命令必须落在用户刚才那个位置上 —— 选区一旦
     丢了，命令就落不到任何段落上，表现就是"点了没反应"。 */
  var lastRange = null;
  function rememberRange() {
    if (!richMode) return;
    var sel = window.getSelection();
    if (!sel || !sel.rangeCount) return;
    var r = sel.getRangeAt(0);
    if (r.startContainer === rich || rich.contains(r.startContainer)) lastRange = r.cloneRange();
  }

  function focusRich() {
    if (!richMode) return;
    var sel = window.getSelection();
    var inside = !!(sel && sel.rangeCount &&
      (sel.anchorNode === rich || rich.contains(sel.anchorNode)));
    rich.focus();
    // 焦点刚从下拉框/外部回来、编辑区里的选区已经塌掉：把光标位置还原回去，
    // 否则这一条命令会安静地落空。
    if (inside || !lastRange) return;
    try {
      sel.removeAllRanges();
      sel.addRange(lastRange);
    } catch (e) { /* 选区已随 DOM 变更失效：不强行恢复，免得比"没反应"更糟 */ }
  }

  function exec(cmd, val) {
    focusRich();
    try { document.execCommand(cmd, false, val || null); } catch (e) { /* 忽略不支持的命令 */ }
    syncFromRich();
    syncToolbarState();
  }

  function insertHtml(html) {
    focusRich();
    document.execCommand('insertHTML', false, html);
    syncFromRich();
  }

  function selectedText() {
    var sel = window.getSelection();
    return sel && sel.rangeCount ? String(sel) : '';
  }

  function toggleInlineCode() {
    var t = selectedText();
    focusRich();
    document.execCommand('insertHTML', false, '<code>' + escHtml(t || '代码') + '</code>');
    syncFromRich();
  }

  function blockIs(tag) {
    // 源码模式下这里呈现的是纯文本，不存在"段落类型"；若不拦，formatBlock 会
    // 落到 textarea 上、把 Markdown 源码改坏。
    if (!richMode) return;
    focusRich();
    document.execCommand('formatBlock', false, tag);
    syncFromRich();
    syncToolbarState();
  }

  /* 光标所在块是不是标题（标题天然加粗） */
  function inHeadingBlock() {
    var sel = window.getSelection();
    if (!sel || !sel.rangeCount) return null;
    var n = sel.getRangeAt(0).startContainer;
    return closestTag(n.nodeType === 1 ? n : n.parentNode, /^H[1-6]$/);
  }

  /* 加粗开关。标题里必须拦下来：
     Markdown 的标题本身就是加粗的，"取消标题里的加粗"无处安放。
     若交给浏览器处理，编辑区会变成浅色（<span style="font-weight:normal">），
     可存回 Markdown 后这个 span 无对应语义、会被丢弃 —— 保存后又变回加粗。
     那就是"所见非所得"：用户以为改了，其实没改。与其骗人，不如说清。 */
  function toggleBold() {
    if (inHeadingBlock()) {
      flashHint('标题本身即为加粗，无需再设；正文里才需要加粗。');
      return;
    }
    exec('bold');
  }

  function insertLink() {
    var t = selectedText();
    var url = window.prompt('链接地址', 'https://');
    if (!url) return;
    var label = t || window.prompt('链接文字', url) || url;
    insertHtml('<a href="' + escHtml(url) + '">' + escHtml(label) + '</a>');
  }

  /* 双链：优先从已有标题里选（下拉），也可以直接输入标题。
     编辑区里只显示链接文字 —— `[[ ]]` 是存储语法，只在真源 Markdown 里出现。
     存回时由 linkMd() 从 href 的 ?title= 还原成 [[标题]] / [[标题|别名]]。 */
  function insertWikilink() {
    var list = window.KB_TITLES || [];
    var tip = list.length
      ? '输入或粘贴要链接的知识页标题（已有 ' + list.length + ' 个标题可供参考）：'
      : '输入要链接的知识页标题：';
    var title = window.prompt(tip, selectedText() || '');
    if (!title) return;
    title = title.trim();
    var label = selectedText().trim();
    var shown = label && label !== title ? label : title;
    insertHtml('<a class="kb-wikilink" title="打开知识页：' + escHtml(title) + '" href="/wiki/?title=' +
      encodeURIComponent(title) + '">' + escHtml(shown) + '</a>');
  }

  function insertTable(rows, cols) {
    var html = '<table><thead><tr>';
    var i, j;
    for (j = 0; j < cols; j++) html += '<th>表头 ' + (j + 1) + '</th>';
    html += '</tr></thead><tbody>';
    for (i = 1; i < rows; i++) {
      html += '<tr>';
      for (j = 0; j < cols; j++) html += '<td><br></td>';
      html += '</tr>';
    }
    html += '</tbody></table>';
    insertHtml(html);
  }

  function insertHr() {
    focusRich();
    document.execCommand('insertHorizontalRule');
    // Chromium 会给新插入的 <hr> 挂一个 id="null"（实现噪声）。清掉它，
    // 免得将来任何"透传未知属性"的路径把这个噪声写进真源。
    var hrs = rich.querySelectorAll('hr');
    for (var i = 0; i < hrs.length; i++) {
      if (hrs[i].getAttribute('id') === 'null') hrs[i].removeAttribute('id');
    }
    syncFromRich();
  }

  function insertTimestamp() {
    var d = new Date();
    function p(n) { return (n < 10 ? '0' : '') + n; }
    insertHtml('【' + d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) +
      ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) + '】');
  }

  function clearFormat() {
    focusRich();
    document.execCommand('removeFormat');
    document.execCommand('unlink');
    syncFromRich();
    syncToolbarState();
  }

  /* -------------------------------------------------------- 表格结构性编辑 */

  function currentCell() {
    var sel = window.getSelection();
    if (!sel || !sel.rangeCount) return null;
    var n = sel.getRangeAt(0).startContainer;
    while (n && n !== rich) {
      if (n.nodeType === 1 && (n.tagName === 'TD' || n.tagName === 'TH')) return n;
      n = n.parentNode;
    }
    return null;
  }

  /* 表格增删行/列都通过对整表的副本做改动、再用 insertHTML 替换原表：
     insertHTML 进浏览器原生撤销栈，Ctrl+Z 能撤；直接改 DOM 则撤不了。 */
  function mutateTable(mutator) {
    var cell = currentCell();
    if (!cell) return;
    var table = cell.parentNode;
    while (table && table.tagName !== 'TABLE') table = table.parentNode;
    if (!table || !table.parentNode) return;
    var tr = cell.parentNode;
    var rows = Array.prototype.slice.call(table.querySelectorAll('tr'));
    var rIdx = rows.indexOf(tr);
    var cIdx = Array.prototype.slice.call(tr.children).indexOf(cell);
    // 用"在本页第几张表"定位，替换后再按同一序号找回新表，比比字符串稳
    var allBefore = Array.prototype.slice.call(rich.querySelectorAll('table'));
    var tIdx = allBefore.indexOf(table);
    var clone = table.cloneNode(true);
    mutator(clone, rIdx, cIdx);
    var sel = window.getSelection();
    var range = document.createRange();
    range.selectNode(table);
    sel.removeAllRanges();
    sel.addRange(range);
    document.execCommand('insertHTML', false, clone.outerHTML);
    var allAfter = Array.prototype.slice.call(rich.querySelectorAll('table'));
    var nTable = allAfter[Math.min(tIdx, allAfter.length - 1)];
    if (nTable) {
      var nRows = nTable.querySelectorAll('tr');
      var nTr = nRows[Math.min(rIdx, nRows.length - 1)];
      if (nTr && nTr.children.length) {
        var nCell = nTr.children[Math.min(cIdx, nTr.children.length - 1)];
        var r2 = document.createRange();
        r2.selectNodeContents(nCell);
        r2.collapse(true);
        sel.removeAllRanges();
        sel.addRange(r2);
      }
    }
    syncFromRich();
    syncTableBar();
  }

  function tableOp(op) {
    mutateTable(function (t, rIdx, cIdx) {
      var rows = Array.prototype.slice.call(t.querySelectorAll('tr'));
      if (op === 'row-add') {
        var tr = rows[rIdx];
        if (!tr) return;
        var copy = document.createElement('tr');
        for (var i = 0; i < tr.children.length; i++) copy.appendChild(document.createElement('td'));
        tr.parentNode.insertBefore(copy, tr.nextSibling);
      } else if (op === 'row-del') {
        if (rows.length <= 1) { t.parentNode.removeChild(t); return; }
        var target = rows[rIdx];
        var body = t.querySelectorAll('tbody tr');
        // 表头行不删：整表失去表头后 Markdown 表格语义就塌了，改删第一行数据
        if (target.parentNode.tagName === 'THEAD' && body.length) {
          body[0].parentNode.removeChild(body[0]);
        } else {
          target.parentNode.removeChild(target);
        }
      } else if (op === 'col-add') {
        if (!rows.length) return;
        for (var r = 0; r < rows.length; r++) {
          var cells = rows[r].children;
          if (!cells.length) continue;
          var ref = cells[Math.min(cIdx, cells.length - 1)];
          var nc = document.createElement(ref.tagName === 'TH' ? 'th' : 'td');
          ref.parentNode.insertBefore(nc, ref.nextSibling);
        }
      } else if (op === 'col-del') {
        if (!rows.length || rows[0].children.length <= 1) {
          t.parentNode.removeChild(t);
          return;
        }
        for (var r2 = 0; r2 < rows.length; r2++) {
          var cs = rows[r2].children;
          var i2 = Math.min(cIdx, cs.length - 1);
          if (cs.length > 1) cs[i2].parentNode.removeChild(cs[i2]);
        }
      } else if (op === 'table-del') {
        t.parentNode.removeChild(t);
      }
    });
  }

  function syncTableBar() {
    if (!tblBar) return;
    var prev = rich.querySelector('.kb-cell-active');
    var cell = richMode ? currentCell() : null;
    if (prev && prev !== cell) prev.classList.remove('kb-cell-active');
    if (!cell || !cell.closest) { tblBar.classList.add('d-none'); return; }
    cell.classList.add('kb-cell-active');
    var table = cell.closest('table');
    var r = table.getBoundingClientRect();
    tblBar.classList.remove('d-none');
    tblBar.style.top = (window.scrollY + r.top - 40) + 'px';
    tblBar.style.left = (window.scrollX + r.left) + 'px';
  }
  document.addEventListener('selectionchange', function () {
    rememberRange();
    syncTableBar();
    syncToolbarState();
  });

  /* --------------------------------------------------- Enter / 快捷键行为 */

  rich.addEventListener('keydown', function (e) {
    var key = e.key;
    if (e.ctrlKey || e.metaKey) {
      if (key === 'b' || key === 'B') { e.preventDefault(); toggleBold(); return; }
      if (key === 'i' || key === 'I') { e.preventDefault(); exec('italic'); return; }
      if (key === 'k' || key === 'K') { e.preventDefault(); insertLink(); return; }
      if (key === 's' || key === 'S') { e.preventDefault(); form.requestSubmit ? form.requestSubmit() : form.submit(); return; }
      if (key >= '1' && key <= '4' && e.shiftKey) { e.preventDefault(); blockIs('<h' + key + '>'); return; }
      if (key === '0' && e.shiftKey) { e.preventDefault(); blockIs('<p>'); return; }
      return;
    }
    if (key !== 'Enter') return;
    var sel = window.getSelection();
    if (!sel || !sel.rangeCount) return;
    var node = sel.getRangeAt(0).startContainer;
    var el = node.nodeType === 1 ? node : node.parentNode;

    // 标题里回车：按人类习惯"标题写完就写正文"，直接落到正文段落
    var h = closestTag(el, /^H[1-6]$/);
    if (h) {
      e.preventDefault();
      var p = document.createElement('p');
      p.appendChild(document.createElement('br'));
      h.parentNode.insertBefore(p, h.nextSibling);
      var r1 = document.createRange();
      r1.selectNodeContents(p); r1.collapse(true);
      sel.removeAllRanges(); sel.addRange(r1);
      syncFromRich();
      return;
    }
    // 空列表项 / 空引用里回车：跳出这个块，而不是无限复制它的符号
    var li = closestTag(el, /^LI$/);
    if (li && !li.textContent.trim()) {
      e.preventDefault();
      blockIs('<p>');
      return;
    }
  });

  function closestTag(el, re) {
    while (el && el !== rich) {
      if (el.nodeType === 1 && re.test(el.tagName)) return el;
      el = el.parentNode;
    }
    return null;
  }

  /* ---------------------------------------------------------- 粘贴处理 */

  var PASTE_OK = { P: 1, BR: 1, STRONG: 1, B: 1, EM: 1, I: 1, CODE: 1, PRE: 1, UL: 1, OL: 1,
    LI: 1, H1: 1, H2: 1, H3: 1, H4: 1, H5: 1, H6: 1, BLOCKQUOTE: 1, A: 1, TABLE: 1,
    THEAD: 1, TBODY: 1, TR: 1, TH: 1, TD: 1, HR: 1, IMG: 1, SPAN: 1, DEL: 1, S: 1 };
  var PASTE_ATTR = { A: { href: 1 }, IMG: { src: 1, alt: 1 }, OL: { start: 1 },
    TH: { style: 1, align: 1, colspan: 1, rowspan: 1 }, TD: { style: 1, align: 1, colspan: 1, rowspan: 1 } };

  function sanitizePasted(html) {
    var doc = new DOMParser().parseFromString(html, 'text/html');
    var body = doc.body;
    (function walk(node) {
      var kids = Array.prototype.slice.call(node.childNodes);
      for (var i = 0; i < kids.length; i++) {
        var n = kids[i];
        if (n.nodeType === 8) { n.parentNode.removeChild(n); continue; }
        if (n.nodeType !== 1) continue;
        if (n.tagName === 'SCRIPT' || n.tagName === 'STYLE' || n.tagName === 'META' ||
            n.tagName === 'LINK' || n.tagName === 'IFRAME' || n.tagName === 'OBJECT') {
          n.parentNode.removeChild(n);
          continue;
        }
        if (!PASTE_OK[n.tagName]) {
          // 不认识的容器（div/section…）连同内容降级为文本，避免带入外部样式
          var text = doc.createTextNode(n.textContent || '');
          n.parentNode.replaceChild(text, n);
          continue;
        }
        var allow = PASTE_ATTR[n.tagName] || {};
        var attrs = Array.prototype.slice.call(n.attributes);
        for (var j = 0; j < attrs.length; j++) {
          var name = attrs[j].name.toLowerCase();
          if (!allow[name]) n.removeAttribute(attrs[j].name);
        }
        if (n.tagName === 'SPAN') {
          var marks = styleMarks(n);
          if (!marks.length) { walk(n); continue; }   // 无可还原语义 → 保留内容，仅清理属性
          var frag = doc.createDocumentFragment();
          var parent = frag;
          for (var m = marks.length - 1; m >= 0; m--) {
            var wrapper = doc.createElement(marks[m] === '**' ? 'strong' : 'em');
            parent.appendChild(wrapper);
            parent = wrapper;
          }
          while (n.firstChild) parent.appendChild(n.firstChild);
          n.parentNode.replaceChild(frag, n);
          continue;
        }
        walk(n);
      }
    })(body);
    return body.innerHTML;
  }

  function looksLikeMarkdown(text) {
    return /(^|\n)\s{0,3}(#{1,6}\s|[-*+]\s|\d+\.\s|>\s|```|\|.*\|)/.test(text) ||
      /\*\*[^*\n]+\*\*/.test(text) || /\[\[[^\]]+\]\]/.test(text);
  }

  rich.addEventListener('paste', function (e) {
    if (!e.clipboardData) return;
    var html = e.clipboardData.getData('text/html');
    var text = e.clipboardData.getData('text/plain') || '';
    e.preventDefault();
    if (html) { insertHtml(sanitizePasted(html)); return; }
    if (text && looksLikeMarkdown(text)) {
      // 粘贴进来的是 Markdown 源码：交给服务端同一渲染器转成富文本再插入，
      // 用户看到的直接是成稿，而不是一堆星号
      fetch(renderUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ text: text })
      }).then(function (r) { return r.json(); }).then(function (d) {
        insertHtml(d && d.ok ? d.html : escHtml(text));
      }).catch(function () { insertHtml(escHtml(text)); });
      return;
    }
    document.execCommand('insertText', false, text);
    syncFromRich();
  });

  function csrfToken() {
    var el = form.querySelector('[name=csrfmiddlewaretoken]');
    return el ? el.value : '';
  }

  /* ------------------------------------------------------------ 工具栏 */

  var TOOLBAR_ACTIONS = {
    bold: toggleBold,
    italic: function () { exec('italic'); },
    code: toggleInlineCode,
    h1: function () { blockIs('<h1>'); },
    h2: function () { blockIs('<h2>'); },
    h3: function () { blockIs('<h3>'); },
    h4: function () { blockIs('<h4>'); },
    p: function () { blockIs('<p>'); },
    ul: function () { exec('insertUnorderedList'); },
    ol: function () { exec('insertOrderedList'); },
    quote: function () { blockIs('<blockquote>'); },
    pre: function () { blockIs('<pre>'); },
    hr: insertHr,
    link: insertLink,
    wikilink: insertWikilink,
    table: function () { insertTable(3, 3); },
    timestamp: insertTimestamp,
    clear: clearFormat
  };

  if (bar) {
    bar.addEventListener('mousedown', function (e) {
      // 只对按钮禁用默认行为：按钮会把焦点抢走，选区在命令执行前就塌了。
      // 原生 <select> 必须放行 —— 它的 mousedown 默认动作就是"展开下拉列表"，
      // 拦掉等于整个下拉框点不动（change 永不触发，看起来就是"点了没反应"）。
      // 下拉框确实会抢走焦点，这个由 focusRich() + rememberRange() 负责把
      // 编辑区里的光标位置找回来。
      if (e.target.closest && e.target.closest('button')) e.preventDefault();
    });
    bar.addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('[data-cmd]') : null;
      if (btn) {
        var fn = TOOLBAR_ACTIONS[btn.getAttribute('data-cmd')];
        if (fn) fn();
        return;
      }
      var opt = e.target.closest ? e.target.closest('[data-block]') : null;
      if (opt) blockIs(opt.getAttribute('data-block'));
    });
    bar.addEventListener('change', function (e) {
      if (e.target.id !== 'mdBlockSelect') return;
      blockIs(e.target.value);
      // 选完把焦点还给正文：焦点若留在下拉框上，用户接着打字会打不进去
      if (richMode) focusRich();
    });
  }

  if (tblBar) {
    tblBar.addEventListener('mousedown', function (e) { e.preventDefault(); });
    tblBar.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('[data-tbl]') : null;
      if (b) tableOp(b.getAttribute('data-tbl'));
    });
  }

  /* 工具栏高亮跟随光标：按钮亮 = 当前选区已是该格式，再点一次就取消 */
  function syncToolbarState() {
    if (!bar) return;
    ['bold', 'italic'].forEach(function (cmd) {
      var b = bar.querySelector('[data-cmd="' + cmd + '"]');
      if (!b) return;
      var on = false;
      try { on = document.queryCommandState(cmd); } catch (e) { on = false; }
      b.classList.toggle('active', !!on);
    });
    var sel = bar.querySelector('#mdBlockSelect');
    if (sel && richMode) {
      var v = '';
      try { v = String(document.queryCommandValue('formatBlock') || '').toLowerCase(); } catch (e) { v = ''; }
      var map = { h1: '<h1>', h2: '<h2>', h3: '<h3>', h4: '<h4>', blockquote: '<blockquote>',
        pre: '<pre>', p: '<p>', div: '<p>', '': '<p>' };
      sel.value = map[v] || '<p>';
    }
  }

  /* -------------------------------------------------------- 模式切换 */

  function setMode(toRich) {
    if (toRich === richMode) return;
    if (toRich) {
      fetch(renderUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ text: src.value })
      }).then(function (r) { return r.json(); }).then(function (d) {
        injectRich(d && d.ok ? d.html : '<p>' + escHtml(src.value) + '</p>');
        richMode = true;
        rich.classList.remove('d-none');
        src.classList.add('d-none');
        if (bar) bar.classList.remove('kb-toolbar-source');
        if (modeBtn) modeBtn.innerHTML = '<i class="bi bi-code-slash"></i> Markdown 源码';
        if (warnEl) warnEl.classList.add('d-none');
        if (tblBar) tblBar.classList.add('d-none');
        updateCount();
        syncToolbarState();
      }).catch(function () { /* 渲染失败则留在源码模式，不冒险 */ });
    } else {
      syncFromRich();
      richMode = false;
      rich.classList.add('d-none');
      src.classList.remove('d-none');
      if (bar) bar.classList.add('kb-toolbar-source');
      if (modeBtn) modeBtn.innerHTML = '<i class="bi bi-magic"></i> 所见即所得';
      if (tblBar) tblBar.classList.add('d-none');
      src.focus();
      updateCount();
    }
  }
  if (modeBtn) modeBtn.addEventListener('click', function () { setMode(!richMode); });

  /* 源码模式下的快捷键也统一到工具栏语义 */
  src.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
      e.preventDefault();
      form.requestSubmit ? form.requestSubmit() : form.submit();
    }
  });

  /* ------------------------------------------------ 草稿恢复 + 保存熔断 */

  function showDraftBar(draft) {
    if (!warnEl) return;
    var when = new Date(draft.at);
    function p(n) { return (n < 10 ? '0' : '') + n; }
    warnEl.innerHTML = '<span><i class="bi bi-clock-history"></i> 本机存有 ' +
      when.getFullYear() + '-' + p(when.getMonth() + 1) + '-' + p(when.getDate()) + ' ' +
      p(when.getHours()) + ':' + p(when.getMinutes()) +
      ' 的草稿（未提交）。</span>' +
      '<button type="button" class="btn btn-sm btn-outline-primary" id="mdDraftUse">恢复草稿</button>' +
      '<button type="button" class="btn btn-sm btn-outline-secondary" id="mdDraftDrop">忽略</button>';
    warnEl.classList.remove('d-none');
    document.getElementById('mdDraftUse').addEventListener('click', function () {
      src.value = draft.md;
      if (richMode) {
        fetch(renderUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
          body: JSON.stringify({ text: draft.md })
        }).then(function (r) { return r.json(); }).then(function (d) {
          injectRich(d && d.ok ? d.html : '<p>' + escHtml(draft.md) + '</p>');
          updateCount();
        });
      }
      warnEl.classList.add('d-none');
    });
    document.getElementById('mdDraftDrop').addEventListener('click', function () {
      try { localStorage.removeItem(draftKey); } catch (e) { /* 忽略 */ }
      warnEl.classList.add('d-none');
    });
  }

  form.addEventListener('submit', function (e) {
    if (richMode) syncFromRich();
    if (forceEl) forceEl.checked = false;
    var before = weight(initialMd);
    var now = weight(src.value);
    if (before >= 40 && now < before * 0.6 && (before - now) > 80) {
      var pct = Math.round((1 - now / before) * 100);
      if (!window.confirm('正文信息量将从约 ' + before + ' 字降到约 ' + now + ' 字（约 -' +
        pct + '%）。\n确认这是有意删改吗？')) {
        e.preventDefault();
        return;
      }
      if (forceEl) forceEl.checked = true;
    }
    try { localStorage.removeItem(draftKey); } catch (err) { /* 忽略 */ }
  });

  /* --------------------------------------------------------------- 启动 */

  window.addEventListener('resize', syncTableBar);
  updateCount();
  syncToolbarState();
  try {
    var raw = localStorage.getItem(draftKey);
    if (raw) {
      var draft = JSON.parse(raw);
      if (draft && draft.md && draft.md !== src.value) showDraftBar(draft);
      else if (draft && draft.md === src.value) localStorage.removeItem(draftKey);
    }
  } catch (e) { /* 草稿可读性失败不影响编辑 */ }
})();
