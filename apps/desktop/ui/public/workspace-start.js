// This control must work even when the application bundle cannot load.
(() => {
  const reload = document.getElementById('startup-reload');
  if (!reload) return;

  const title = document.getElementById('startup-title');
  const hint = document.getElementById('startup-hint');
  if (!reload.disabled) {
    hint.textContent = 'If the workspace stays here, reload this window.';
    reload.addEventListener('click', () => {
      if (!reload.disabled) window.location.reload();
    }, { once: true });
    reload.hidden = false;
  }

  window.setTimeout(() => {
    if (title.isConnected) title.textContent = 'The workspace hasn’t opened yet';
  }, 10_000);
})();
