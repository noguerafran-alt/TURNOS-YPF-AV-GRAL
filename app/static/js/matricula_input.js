/**
 * Matrícula: solo letras/números, sin espacios ni guiones, mayúsculas en vivo.
 * Aplica a inputs con name/id típicos o [data-matricula-input].
 */
(function () {
  'use strict';

  var SELECTOR = [
    'input[name="matricula"]',
    'input[name="aircraft"]',
    'input[name="aircraft_confirm"]',
    'input[id="matricula"]',
    'input[id="aircraft"]',
    'input[id="aircraft_confirm"]',
    'input[id="man-aircraft"]',
    'input[id="f-mat"]',
    'input[data-matricula-input]',
    'input.js-matricula'
  ].join(',');

  function normalize(value) {
    return String(value || '')
      .toUpperCase()
      .replace(/[^A-Z0-9]/g, '');
  }

  function applyTo(el) {
    if (!el || el.dataset.matriculaBound === '1') return;
    if (el.tagName !== 'INPUT') return;
    el.dataset.matriculaBound = '1';
    el.setAttribute('autocomplete', 'off');
    el.setAttribute('spellcheck', 'false');
    el.setAttribute('inputmode', 'text');
    if (!el.getAttribute('maxlength')) el.setAttribute('maxlength', '40');
    if ((el.placeholder || '').indexOf('-') !== -1) {
      el.placeholder = normalize(el.placeholder) || 'LVABC';
    }
    // valor inicial (edición)
    if (el.value) {
      var n0 = normalize(el.value);
      if (n0 !== el.value) el.value = n0;
    }

    function scrub(fromEvent) {
      var start = el.selectionStart;
      var end = el.selectionEnd;
      var before = el.value;
      var after = normalize(before);
      if (after === before) return;
      el.value = after;
      if (typeof start === 'number' && document.activeElement === el) {
        // aproximar caret: contar alfanum antes del caret
        var kept = 0;
        for (var i = 0; i < start && i < before.length; i++) {
          if (/[A-Za-z0-9]/.test(before.charAt(i))) kept++;
        }
        try { el.setSelectionRange(kept, kept); } catch (_) {}
      }
      if (fromEvent) {
        el.dispatchEvent(new Event('input', { bubbles: true }));
      }
    }

    el.addEventListener('input', function () { scrub(false); });
    el.addEventListener('blur', function () { scrub(false); });
    el.addEventListener('paste', function () {
      setTimeout(function () { scrub(false); }, 0);
    });
  }

  function scan(root) {
    var scope = root && root.querySelectorAll ? root : document;
    scope.querySelectorAll(SELECTOR).forEach(applyTo);
    if (root && root.matches && root.matches(SELECTOR)) applyTo(root);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { scan(document); });
  } else {
    scan(document);
  }

  var mo = new MutationObserver(function (muts) {
    muts.forEach(function (m) {
      m.addedNodes && m.addedNodes.forEach(function (n) {
        if (n.nodeType === 1) scan(n);
      });
    });
  });
  mo.observe(document.documentElement, { childList: true, subtree: true });

  window.YPFNormalizeMatricula = normalize;
})();
