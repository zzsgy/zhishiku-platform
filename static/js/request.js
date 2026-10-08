/* Shared same-origin CSRF policy, including existing page-specific fetch calls. */
(function () {
  var original = window.fetch;
  if (!original) return;
  window.fetch = function (input, options) {
    var request = new Request(input, options);
    var url = new URL(request.url, window.location.href);
    if (url.origin === window.location.origin && !/^(GET|HEAD|OPTIONS|TRACE)$/i.test(request.method)) {
      var headers = new Headers(request.headers);
      var token = document.querySelector('meta[name="csrf-token"]');
      if (token) headers.set('X-CSRFToken', token.content);
      request = new Request(request, { headers: headers, credentials: 'same-origin' });
    }
    return original.call(window, request);
  };
})();
