(() => {
  const form = document.getElementById('transport-form');
  if (!form) return;
  const list = document.getElementById('transport-items');
  const total = document.getElementById('id_items-TOTAL_FORMS');
  const field = (card, name) => card.querySelector(`[name$="-${name}"]`);
  const cards = () => [...list.querySelectorAll('.transport-item')].filter(c => !field(c, 'DELETE').checked);
  function deliveryNote() {
    const numbers = document.getElementById('id_place').value.split(',').map(part =>
      part.trim().replace(/^(?:площадки|площадка|пл\.?)\s*(?=№|\d|\s|$)/i, '').trim().replace(/^№+\s*/, '')
    ).filter(Boolean);
    return numbers.length ? 'Подача на ' + (numbers.length === 1 ? 'площадку ' : 'площадки ') + numbers.map(n => '№' + n).join(', ') : '';
  }
  let step = 1;
  function showStep(n) {
    step = n;
    document.getElementById('step-1').hidden = n !== 1;
    document.getElementById('step-2').hidden = n !== 2;
    document.getElementById('next-step').hidden = n !== 1;
    document.getElementById('save-request').hidden = n !== 2;
    form.querySelectorAll('.step-button').forEach(b => {
      b.classList.toggle('btn-primary', Number(b.dataset.step) === n);
      b.classList.toggle('btn-outline-primary', Number(b.dataset.step) !== n);
      b.setAttribute('aria-current', Number(b.dataset.step) === n ? 'step' : 'false');
    });
  }
  function validate(section) {
    for (const el of section.querySelectorAll('input,select')) {
      if (!el.disabled && !el.checkValidity()) { el.reportValidity(); return false; }
    }
    return true;
  }
  function update(card) {
    const period = field(card, 'mode').value === 'period';
    card.querySelector('.field-end').hidden = !period;
    card.querySelector('.field-frequency').hidden = !period;
    field(card, 'end').disabled = !period;
    field(card, 'end').required = period;
    card.querySelector('.field-start .transport-field-label').textContent = period ? 'С' : 'Дата';
    cards().forEach((entry, index) => { entry.querySelector('.transport-item-title').textContent = `Транспорт №${index + 1}`; });
    const start = field(card, 'start').value, end = period ? field(card, 'end').value : start;
    field(card, 'end').setCustomValidity(period && start && end && end < start ? 'Дата окончания раньше даты начала.' : '');
    let dates = [];
    if (period && field(card, 'frequency').value === 'alternate' && start && end) {
      for (let t = Date.parse(start + 'T00:00:00Z'), stop = Date.parse(end + 'T00:00:00Z'); t <= stop && dates.length < 183; t += 172800000) dates.push(new Date(t).toLocaleDateString('ru-RU', {timeZone: 'UTC'}));
    }
    card.querySelector('.schedule-dates').textContent = dates.length ? 'Даты работы: ' + dates.join(', ') : '';
    const starts = cards().map(c => field(c, 'start').value).filter(Boolean).sort();
    const ends = cards().map(c => field(c, field(c, 'mode').value === 'day' ? 'start' : 'end').value).filter(Boolean).sort();
    document.getElementById('period-summary').textContent = starts.length && ends.length ? `Общий период: ${starts[0].split('-').reverse().join('.')} — ${ends.at(-1).split('-').reverse().join('.')}` : '';
  }
  function add(source) {
    if (Number(total.value) >= 100) { document.getElementById('transport-error').textContent = 'Не более 100 позиций. Сохраните заявку перед добавлением новых вместо удалённых.'; return; }
    const html = document.getElementById('empty-item').innerHTML.replaceAll('__prefix__', total.value);
    list.insertAdjacentHTML('beforeend', html);
    total.value = Number(total.value) + 1;
    const card = list.lastElementChild;
    if (source) {
      ['vehicle','quantity','mode','start','end','shift','frequency','work','note'].forEach(k => { field(card, k).value = field(source, k).value; });
    } else {
      field(card, 'start').value = document.getElementById('id_date').value;
      field(card, 'note').value = deliveryNote();
    }
    update(card);
  }
  list.addEventListener('change', e => { const c = e.target.closest('.transport-item'); if (c) update(c); });
  list.addEventListener('click', e => {
    const c = e.target.closest('.transport-item'); if (!c) return;
    if (e.target.closest('.copy-item')) add(c);
    if (e.target.closest('.remove-item')) {
      field(c, 'DELETE').checked = true;
      c.hidden = true;
      c.querySelectorAll('input,select').forEach(el => { el.required = false; el.setCustomValidity(''); });
      update(c);
      field(c, 'end').required = false;
    }
  });
  document.getElementById('add-item').onclick = () => add();
  document.getElementById('next-step').onclick = () => {
    if (validate(document.getElementById('step-1'))) {
      cards().forEach(c => {
        if (!field(c, 'note').value && !field(c, 'id').value) field(c, 'note').value = deliveryNote();
      });
      showStep(2);
    }
  };
  form.querySelectorAll('.step-button').forEach(b => { b.onclick = () => showStep(Number(b.dataset.step)); });
  form.addEventListener('submit', e => {
    if (!validate(document.getElementById('step-1'))) { e.preventDefault(); showStep(1); validate(document.getElementById('step-1')); return; }
    if (!cards().length) { e.preventDefault(); document.getElementById('transport-error').textContent = 'Добавьте хотя бы одну позицию транспорта.'; return; }
    for (const c of cards()) if (!validate(c)) { e.preventDefault(); showStep(2); return; }
    const submit = document.getElementById('save-request');
    submit.disabled = true;
    submit.textContent = 'Формирование заявки…';
  });
  list.querySelectorAll('.transport-item').forEach(c => { if (field(c,'DELETE').checked) c.hidden = true; else update(c); });
  if (!Number(total.value)) add();
  showStep(Number(form.dataset.startStep) === 2 ? 2 : 1);
})();
