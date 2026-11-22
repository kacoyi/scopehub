// The basic recovery action must work while either application module is unavailable.
(() => {
  const reload = document.getElementById('startup-reload');
  if (reload && !reload.disabled) {
    reload.addEventListener('click', () => {
      if (!reload.disabled) window.location.reload();
    }, { once: true });
    reload.hidden = false;
  }
})();
