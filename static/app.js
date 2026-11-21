// Без инлайн-скриптов (политика CSP): всё подключается здесь.
document.documentElement.classList.add('js');

// Подтверждение опасных действий: <form data-confirm="Текст вопроса?">
document.addEventListener('submit', (e) => {
  const message = e.target.dataset && e.target.dataset.confirm;
  if (message && !window.confirm(message)) e.preventDefault();
});

// Свой цвет: выбор в палитре сразу сохраняется
document.addEventListener('change', (e) => {
  if (e.target.matches('[data-autosubmit]')) e.target.form.submit();
});

// Живой поиск и фильтры: обновляем только список товаров, без перезагрузки страницы
const filters = document.getElementById('filters');
if (filters) {
  const apply = filters.querySelector('.apply');
  if (apply) apply.style.display = 'none';
  let timer = null;
  let counter = 0;

  const update = async () => {
    const params = new URLSearchParams(new FormData(filters));
    const url = filters.getAttribute('action') + '?' + params.toString();
    const ticket = ++counter;
    try {
      const response = await fetch(url, { headers: { 'X-Requested-With': 'fetch' }, credentials: 'same-origin' });
      if (!response.ok || ticket !== counter) return;
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      const fresh = doc.getElementById('results');
      const current = document.getElementById('results');
      if (fresh && current) current.replaceWith(fresh);
      history.replaceState(null, '', url);
    } catch (err) {
      filters.submit();  // сеть недоступна — обычная отправка формы
    }
  };

  filters.addEventListener('input', (e) => {
    clearTimeout(timer);
    timer = setTimeout(update, e.target.type === 'text' ? 300 : 0);
  });
  filters.addEventListener('submit', (e) => { e.preventDefault(); update(); });
}
