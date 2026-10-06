(() => {
  const root = document.getElementById('profile-settings');
  if (!root) return;
  root.querySelectorAll('.profile-tabs a').forEach(link => link.addEventListener('click', () => {
    root.querySelectorAll('.profile-tabs a').forEach(item => item.classList.toggle('selected', item === link));
  }));
  function revealSection() {
    const drawer = [...root.querySelectorAll('.settings-drawer')].find(el => el.dataset.section === location.hash.slice(1));
    if (drawer && window.bootstrap) bootstrap.Offcanvas.getOrCreateInstance(drawer).show();
  }
  const forms = [];
  root.querySelectorAll('.settings-drawer').forEach(drawer => {
    const form = drawer.querySelector('form');
    const snapshot = () => JSON.stringify([...new FormData(form).entries()]);
    let baseline = snapshot(), submitting = false;
    const dirty = () => !submitting && snapshot() !== baseline;
    forms.push(dirty);
    form.addEventListener('submit', () => { submitting = true; });
    drawer.addEventListener('hide.bs.offcanvas', event => {
      if (dirty() && !window.confirm('Изменения не сохранены. Закрыть без сохранения?')) event.preventDefault();
    });
    drawer.addEventListener('hidden.bs.offcanvas', () => { form.reset(); baseline = snapshot(); });
    if (drawer.dataset.errors === '1' && window.bootstrap) bootstrap.Offcanvas.getOrCreateInstance(drawer).show();
  });
  window.addEventListener('beforeunload', event => { if (forms.some(dirty => dirty())) { event.preventDefault(); event.returnValue = ''; } });
  revealSection();
  window.addEventListener('hashchange', revealSection);
})();
