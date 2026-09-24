(function () {
  var theme = document.documentElement.getAttribute('data-theme') || 'light';
  function apply(t) {
    document.documentElement.setAttribute('data-theme', t);
    try { localStorage.setItem('kb_theme', t); } catch (e) {}
  }
  try {
    var saved = localStorage.getItem('kb_theme');
    if (saved) apply(saved);
  } catch (e) {}
  var btn = document.getElementById('themeToggle');
  if (btn) {
    btn.addEventListener('click', function () {
      theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      apply(theme);
    });
  }
})();
