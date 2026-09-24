/* 划句工具条：选中正文文字后浮动出现「问 AI / 存灵感 / 存金句」。
   依赖两个后端 API：/wiki/api/ask/、/inspiration/api/add/、/bookshelf/api/golden/ */
(function () {
  if (window.__kbSel) return;
  window.__kbSel = true;

  var bar = document.createElement('div');
  bar.id = 'kbSelBar';
  bar.innerHTML = '<button data-act="ai">问 AI</button>'
    + '<button data-act="insp">存灵感</button>'
    + '<button data-act="gold">存金句</button>';
  // 书籍阅读页 / 知识库节点阅读页 额外提供「记笔记」
  if (window.KB_BOOK_ID || window.KB_NODE_ID) {
    bar.innerHTML += '<button data-act="note">记笔记</button>';
  }
  document.body.appendChild(bar);

  var modal = document.createElement('div');
  modal.id = 'kbAiModal';
  modal.innerHTML = ''
    + '<div class="kb-ai-box">'
    +   '<div class="kb-ai-head">AI 解读 <button id="kbAiClose" aria-label="关闭">×</button></div>'
    +   '<div class="kb-ai-body">'
    +     '<select id="kbAiProvider" class="form-select form-select-sm mb-2"><option value="">默认服务</option></select>'
    +     '<input id="kbAiQ" placeholder="想问什么？（回车发送，默认：解读这段）">'
    +     '<button id="kbAiSend">解读</button>'
    +   '</div>'
    +   '<div class="kb-ai-a" id="kbAiA">…</div>'
    + '</div>';
  document.body.appendChild(modal);

  // 加载可选的 AI 服务
  function loadProviders() {
    fetch('/api/providers/').then(function (r) { return r.json(); }).then(function (d) {
      var sel = document.getElementById('kbAiProvider');
      if (!sel || !d.ok) return;
      sel.innerHTML = '<option value="">默认服务</option>';
      (d.providers || []).forEach(function (p) {
        var o = document.createElement('option');
        o.value = p.id; o.textContent = p.name + (p.configured ? '' : '（未配置）');
        sel.appendChild(o);
      });
    }).catch(function () {});
  }

  var selText = '';

  function inProse(node) {
    var n = node;
    while (n && n !== document.body) {
      if (n.classList && n.classList.contains('kb-prose')) return true;
      n = n.parentNode;
    }
    return false;
  }

  function showBar() {
    var s = window.getSelection();
    if (!s || s.isCollapsed) { bar.style.display = 'none'; return; }
    var txt = s.toString().trim();
    if (!txt) { bar.style.display = 'none'; return; }
    var range = s.getRangeAt(0);
    if (!inProse(range.commonAncestorContainer)) { bar.style.display = 'none'; return; }
    selText = txt;
    var rect = range.getBoundingClientRect();
    bar.style.display = 'flex';
    bar.style.left = (window.scrollX + rect.left) + 'px';
    bar.style.top = (window.scrollY + rect.top - 38) + 'px';
  }

  document.addEventListener('mouseup', function (e) {
    if (bar.contains(e.target)) return;
    setTimeout(showBar, 10);
  });
  bar.addEventListener('mousedown', function (e) { e.preventDefault(); });

  bar.addEventListener('click', function (e) {
    var act = e.target.getAttribute('data-act');
    if (!act) return;
    if (act === 'ai') openAi();
    else if (act === 'insp') postJSON('/inspiration/api/add/', { text: selText, source: pageRef() }, '已收入灵感库');
    else if (act === 'gold') addGolden();
    else if (act === 'note') addNote();
  });

  // 划词存金句：写入金句库并绑定归属（书籍/节点），并在阅读页「本文/本书金句」即时前插
  function addGolden() {
    var text = selText;
    var payload = { text: text, source: pageRef() };
    if (window.KB_NODE_ID) payload.node = window.KB_NODE_ID;
    else if (window.KB_BOOK_ID) payload.book = window.KB_BOOK_ID;
    fetch('/bookshelf/api/golden/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
      body: JSON.stringify(payload)
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { toast('失败：' + (d.error || '')); return; }
        toast('已收入金句库');
        var list = document.getElementById('kbGoldenList');
        if (list) {
          var empty = list.querySelector('.kb-golden-empty');
          if (empty) empty.remove();
          list.insertBefore(makeGoldenLi(text, d.id), list.firstChild);
          var cnt = document.getElementById('kbGoldenCount');
          if (cnt) cnt.textContent = parseInt(cnt.textContent || '0', 10) + 1;
        }
      })
      .catch(function () { toast('网络错误'); });
  }

  // 构建「金句」列表项（含即时删除按钮，data-id 取自后端返回）
  function makeGoldenLi(text, id) {
    var li = document.createElement('li');
    li.className = 'list-group-item';
    var row = document.createElement('div');
    row.className = 'd-flex justify-content-between align-items-start gap-2';
    var textWrap = document.createElement('div');
    textWrap.className = 'flex-fill';
    var icon = document.createElement('i');
    icon.className = 'bi bi-quote text-warning';
    textWrap.appendChild(icon);
    textWrap.appendChild(document.createTextNode(' ' + text));
    row.appendChild(textWrap);
    row.appendChild(makeDelBtn('golden', id));
    li.appendChild(row);
    return li;
  }

  // 构建「笔记」列表项（含即时删除按钮）
  function makeNoteLi(text, id, type) {
    var li = document.createElement('li');
    li.className = 'list-group-item';
    var now = new Date();
    var pad = function (x) { return (x < 10 ? '0' : '') + x; };
    var meta = document.createElement('div');
    meta.className = 'small text-muted';
    meta.textContent = '✍ 划词 · ' + pad(now.getMonth() + 1) + '-' + pad(now.getDate())
      + ' ' + pad(now.getHours()) + ':' + pad(now.getMinutes());
    li.appendChild(meta);
    var row = document.createElement('div');
    row.className = 'd-flex justify-content-between align-items-start gap-2';
    var textWrap = document.createElement('div');
    textWrap.className = 'flex-fill';
    textWrap.textContent = text;
    row.appendChild(textWrap);
    row.appendChild(makeDelBtn(type, id));
    li.appendChild(row);
    return li;
  }

  // 统一的删除按钮（data-type 区分 note / node_note / golden，决定后端路由）
  function makeDelBtn(type, id) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-sm btn-link text-danger p-0 kb-del';
    btn.setAttribute('data-type', type);
    btn.setAttribute('data-id', id);
    btn.title = (type === 'golden') ? '删除金句' : '删除笔记';
    var ic = document.createElement('i');
    ic.className = 'bi bi-trash';
    btn.appendChild(ic);
    return btn;
  }

  // 划词记笔记：写入当前书籍 / 知识节点的阅读笔记，并即时前插到右侧列表
  function addNote() {
    if (window.KB_NODE_ID) {
      addNoteTo('/knowledgebase/api/note/', { node: window.KB_NODE_ID, location: '', note: selText }, '已加入阅读笔记');
    } else if (window.KB_BOOK_ID) {
      addNoteTo('/bookshelf/api/note/', { book: window.KB_BOOK_ID, location: '', note: selText }, '已加入读书笔记');
    }
  }

  function addNoteTo(url, payload, okMsg) {
    var type = (url.indexOf('/knowledgebase/') === 0) ? 'node_note' : 'note';
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
      body: JSON.stringify(payload)
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { toast('失败：' + (d.error || '')); return; }
        toast(okMsg);
        var list = document.getElementById('kbNoteList');
        if (list) {
          var empty = list.querySelector('.kb-note-empty');
          if (empty) empty.remove();
          list.insertBefore(makeNoteLi(selText, d.id, type), list.firstChild);
          var cnt = document.getElementById('kbNoteCount');
          if (cnt) cnt.textContent = parseInt(cnt.textContent || '0', 10) + 1;
        }
      })
      .catch(function () { toast('网络错误'); });
  }

  function pageRef() {
    var h = document.querySelector('h2');
    return h ? h.textContent.trim() : '阅读页';
  }
  function getCookie(n) {
    var v = null;
    document.cookie.split(';').forEach(function (c) {
      var p = c.trim().split('=');
      if (p[0] === n) v = decodeURIComponent(p[1]);
    });
    return v;
  }
  function postJSON(url, data, okMsg) {
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
      body: JSON.stringify(data)
    })
      .then(function (r) { return r.json(); })
      .then(function (d) { toast(d.ok ? okMsg : ('失败：' + (d.error || ''))); })
      .catch(function () { toast('网络错误'); });
  }
  function openAi() {
    modal.style.display = 'flex';
    var q = document.getElementById('kbAiQ');
    var a = document.getElementById('kbAiA');
    q.value = ''; a.textContent = '…'; q.focus();
    loadProviders();
    document.getElementById('kbAiClose').onclick = function () { modal.style.display = 'none'; };
    document.getElementById('kbAiSend').onclick = function () {
      var question = q.value.trim() || '请解读这段内容的核心要点';
      var provider = document.getElementById('kbAiProvider').value;
      a.textContent = '思考中…';
      // 依据当前阅读面选择问 AI 端点：书籍阅读页携带书本上下文，知识节点走 WiKI 端点
      var endpoint = window.KB_BOOK_ID ? '/bookshelf/api/ask/' : '/wiki/api/ask/';
      var payload = { text: selText, question: question, provider: provider };
      if (window.KB_BOOK_ID) payload.book = window.KB_BOOK_ID;
      fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        body: JSON.stringify(payload)
      })
        .then(function (r) { return r.json(); })
        .then(function (d) { a.textContent = d.ok ? d.answer : ('错误：' + (d.error || '')); })
        .catch(function () { a.textContent = '网络错误'; });
    };
  }
  function toast(msg) {
    var t = document.createElement('div');
    t.className = 'kb-toast';
    t.textContent = msg;
    document.body.appendChild(t);
    requestAnimationFrame(function () { t.classList.add('show'); });
    setTimeout(function () { t.remove(); }, 2600);
  }

  // 批注台手动删除：笔记与金句仅在用户确认删除时才移除（关闭阅读页不会丢失）
  document.addEventListener('click', function (e) {
    var btn = (e.target && e.target.closest) ? e.target.closest('.kb-del') : null;
    if (!btn) return;
    e.preventDefault();
    var type = btn.getAttribute('data-type');
    var id = btn.getAttribute('data-id');
    if (!type || !id) return;
    if (!window.confirm('确定删除该条内容？删除后不会自动恢复，需重新添加。')) return;
    var endpoint = null;
    if (type === 'note') endpoint = '/bookshelf/api/note_delete/';
    else if (type === 'node_note') endpoint = '/knowledgebase/api/note_delete/';
    else if (type === 'golden') endpoint = '/bookshelf/api/golden_delete/';
    if (!endpoint) return;
    fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
      body: JSON.stringify({ id: id })
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { toast('删除失败：' + (d.error || '')); return; }
        toast('已删除');
        var li = (btn.closest) ? btn.closest('li') : null;
        if (li && li.parentNode) {
          var list = li.parentNode;
          li.remove();
          var cnt = (list.id === 'kbGoldenList')
            ? document.getElementById('kbGoldenCount')
            : document.getElementById('kbNoteCount');
          if (cnt) cnt.textContent = Math.max(0, parseInt(cnt.textContent || '0', 10) - 1);
          if (!list.querySelector('.list-group-item')) {
            var empt = document.createElement('li');
            empt.className = 'list-group-item text-muted small '
              + (list.id === 'kbGoldenList' ? 'kb-golden-empty' : 'kb-note-empty');
            empt.textContent = (list.id === 'kbGoldenList')
              ? '划句时选「存金句」即可收录。' : '还没有笔记。选中正文文字可快速「记笔记」。';
            list.appendChild(empt);
          }
        }
      })
      .catch(function () { toast('网络错误'); });
  });
})();
