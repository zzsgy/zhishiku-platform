(function () {
  var button = document.getElementById('passwordToggle');
  var input = document.getElementById('id_password');
  if (!button || !input) return;
  button.addEventListener('click', function () {
    var show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    button.setAttribute('aria-pressed', String(show));
    button.setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
    button.querySelector('i').className = show ? 'bi bi-eye-slash' : 'bi bi-eye';
  });
})();
