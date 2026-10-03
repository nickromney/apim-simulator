(() => {
  const control = document.getElementById('theme-select');
  const media = matchMedia('(prefers-color-scheme: dark)');
  let preference = 'system';
  try { const saved = localStorage.getItem('apim-appearance'); if (['light','dark','system'].includes(saved)) preference = saved; } catch {}
  control.value = preference;
  const apply = () => {
    document.documentElement.dataset.theme = preference === 'system' ? (media.matches ? 'dark' : 'light') : preference;
    window.dispatchEvent(new Event('apim-theme-change'));
  };
  control.addEventListener('change', () => { preference = control.value; try { localStorage.setItem('apim-appearance', preference); } catch {} apply(); });
  media.addEventListener('change', apply);
  apply();
})();
