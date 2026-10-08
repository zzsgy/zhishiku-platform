(function () {
  try {
    var saved = localStorage.getItem('kb_theme');
    if (saved === 'light' || saved === 'dark') document.documentElement.setAttribute('data-theme', saved);
  } catch (error) { /* Theme defaults remain available without storage. */ }
})();
