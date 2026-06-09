(function () {
  var STORAGE_KEY = 'mileage-tracker-theme';

  function getSystemTheme() {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    var toggle = document.getElementById('theme-toggle');
    if (toggle) {
      var isDark = theme === 'dark';
      toggle.setAttribute('aria-label', isDark ? 'Switch to light mode' : 'Switch to dark mode');
      toggle.setAttribute('title', isDark ? 'Light mode' : 'Dark mode');
    }
  }

  function initTheme() {
    var stored = localStorage.getItem(STORAGE_KEY);
    var theme = stored || getSystemTheme();
    applyTheme(theme);
  }

  function toggleTheme() {
    var current = document.documentElement.getAttribute('data-theme') || 'light';
    var next = current === 'dark' ? 'light' : 'dark';
    localStorage.setItem(STORAGE_KEY, next);
    applyTheme(next);
  }

  function initNav() {
    var toggle = document.getElementById('nav-toggle');
    var nav = document.getElementById('primary-nav');
    var overlay = document.getElementById('nav-overlay');
    if (!toggle || !nav) return;

    function closeNav() {
      nav.classList.remove('open');
      if (overlay) overlay.classList.remove('open');
      toggle.setAttribute('aria-expanded', 'false');
    }

    function openNav() {
      nav.classList.add('open');
      if (overlay) overlay.classList.add('open');
      toggle.setAttribute('aria-expanded', 'true');
    }

    toggle.addEventListener('click', function () {
      if (nav.classList.contains('open')) {
        closeNav();
      } else {
        openNav();
      }
    });

    if (overlay) {
      overlay.addEventListener('click', closeNav);
    }

    nav.querySelectorAll('.nav-link').forEach(function (link) {
      link.addEventListener('click', function () {
        if (window.innerWidth < 768) closeNav();
      });
    });

    window.addEventListener('resize', function () {
      if (window.innerWidth >= 768) closeNav();
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    initTheme();
    initNav();
    var themeBtn = document.getElementById('theme-toggle');
    if (themeBtn) {
      themeBtn.addEventListener('click', toggleTheme);
    }
  });
})();
