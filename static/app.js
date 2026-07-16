const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); }

let menu = [];
let cart = {};
let active = 'Все';
let statusTimer = null;
let favorites = new Set(JSON.parse(localStorage.getItem('soblazn_favorites') || '[]').map(Number));

const money = n => new Intl.NumberFormat('ru-RU').format(Number(n || 0)) + ' ₸';
const esc = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

fetch('/api/menu')
  .then(r => r.json())
  .then(d => {
    menu = Array.isArray(d.items) ? d.items : [];
    renderCats();
    render();
    updateCart();
  })
  .catch(() => {
    products.innerHTML = '<div class="empty-state"><div>⚠️</div><b>Меню временно недоступно</b><small>Обновите страницу через минуту</small></div>';
  });

function categories() { return ['Все', ...new Set(menu.map(x => x.category).filter(Boolean))]; }
function renderCats() {
  cats.innerHTML = categories().map(c => `<button class="${c === active ? 'active' : ''}" onclick='setCategory(${JSON.stringify(c)})'>${esc(c)}</button>`).join('');
}
function setCategory(category){ active=category; renderCats(); render(); }
function emoji(cat) {
  const text = String(cat || '').toLowerCase();
  const map = [[['чебур','варен','пельмен'],'🥟'],[['пицц'],'🍕'],[['шашлык','гриль','мяс'],'🍖'],[['салат'],'🥗'],[['суп','перв'],'🍲'],[['бургер'],'🍔'],[['закуск','фри','гарнир'],'🍟'],[['десерт','слад'],'🍰'],[['напит','лимонад','вода'],'🥤'],[['завтрак'],'🍳'],[['паста'],'🍝']];
  for (const [keys, icon] of map) if (keys.some(k => text.includes(k))) return icon;
  return '🍽️';
}
function badgeFor(p){
  const text=(p.name+' '+p.category).toLowerCase();
  if(text.includes('чебур')) return 'Хит';
  if(text.includes('бургер')||text.includes('пицц')) return 'Популярное';
  if(Number(p.id)%7===0) return 'Рекомендуем';
  return '';
}
function imageFor(p){ return p.image || p.photo || p.image_url || p.photo_url || ''; }
function descriptionFor(p){ return p.description || p.desc || 'Готовим после оформления заказа'; }
function render() {
  const q = String(search.value || '').trim().toLowerCase();
  const list = menu.filter(x => (active === 'Все' || x.category === active) && String(x.name || '').toLowerCase().includes(q));
  products.innerHTML = list.map(p => {
    const n = cart[p.id] || 0;
    const image = imageFor(p);
    const badge = badgeFor(p);
    const fav = favorites.has(Number(p.id));
    return `<article class="product">
      <div class="thumb">
        ${image ? `<img src="${esc(image)}" alt="${esc(p.name)}" loading="lazy" onerror="this.remove();this.parentNode.querySelector('.food-emoji').style.display='block'">` : ''}
        <span class="food-emoji" ${image ? 'style="display:none"' : ''}>${emoji(p.category)}</span>
        ${badge ? `<span class="badge">${badge}</span>` : ''}
        <button class="fav-btn ${fav ? 'active' : ''}" onclick="toggleFavorite(${Number(p.id)});event.stopPropagation()" aria-label="Избранное">${fav ? '♥' : '♡'}</button>
      </div>
      <div class="product-body"><h3>${esc(p.name)}</h3><p class="product-desc">${esc(descriptionFor(p))}</p>
      <div class="product-foot"><div class="price">${money(p.price)}</div>
      ${n ? `<div class="qty"><button onclick="change(${Number(p.id)},-1)">−</button><b>${n}</b><button onclick="change(${Number(p.id)},1)">+</button></div>` : `<button class="add" onclick="change(${Number(p.id)},1)">Добавить</button>`}
      </div></div></article>`;
  }).join('') || '<div class="empty-state"><div>🔎</div><b>Ничего не найдено</b><small>Попробуйте другую категорию или запрос</small></div>';
}
function toggleFavorite(id){ favorites.has(id) ? favorites.delete(id) : favorites.add(id); localStorage.setItem('soblazn_favorites',JSON.stringify([...favorites])); tg?.HapticFeedback?.selectionChanged(); render(); }
function showFavorites(){
  const list=menu.filter(p=>favorites.has(Number(p.id)));
  modalBody.innerHTML=`<h2>Избранное</h2>${list.length?list.map(p=>`<div class="favorite-card"><div><b>${esc(p.name)}</b><br><small>${money(p.price)}</small></div><button onclick="change(${Number(p.id)},1);showFavorites()">Добавить</button></div>`).join(''):'<div class="empty-state"><div>♡</div><b>Пока пусто</b><small>Нажимайте сердечко на любимых блюдах</small></div>'}`;
  modal.classList.remove('hidden');
}
search.oninput = render;
cartTop.onclick = openCart;
function change(id, d) { cart[id] = Math.max(0, (cart[id] || 0) + d); if (!cart[id]) delete cart[id]; tg?.HapticFeedback?.impactOccurred('light'); render(); updateCart(); }
function cartArray() { return Object.entries(cart).map(([id, qty]) => ({id:Number(id),qty:Number(qty)})); }
function updateCart() {
  const count=Object.values(cart).reduce((a,b)=>a+b,0);
  const total=Object.entries(cart).reduce((sum,[id,qty])=>{const p=menu.find(x=>Number(x.id)===Number(id));return sum+(p?p.price*qty:0)},0);
  cartCount.textContent=count; barCount.textContent=count+' поз.'; barTotal.textContent=money(total); cartBar.classList.toggle('hidden',!count);
}

async function openCart() {
  if (!cartArray().length) return;

  const calc = await fetch('/api/calculate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({cart: cartArray()})
  }).then(r => r.json());

  modalBody.innerHTML = `
    <h2>Корзина</h2>
    ${calc.items.map(x => `
      <div class="cartrow">
        <div><b>${x.name}</b><br><small>${money(x.price)} × ${x.qty}</small></div>
        <div>
          <button onclick="change(${x.id},-1);openCart()">−</button>
          ${x.qty}
          <button onclick="change(${x.id},1);openCart()">+</button>
        </div>
      </div>
    `).join('')}
    <div class="totals">
      <div><span>Блюда</span><b>${money(calc.subtotal)}</b></div>
      <div><span>Упаковка</span><b>${money(calc.packaging_fee)}</b></div>
      <div><span>Доставка</span><b>${money(calc.delivery_fee)}</b></div>
      <div class="grand"><span>Итого</span><b>${money(calc.total)}</b></div>
    </div>
    <button class="primary" onclick="checkout()">Оформить заказ</button>
  `;
  modal.classList.remove('hidden');
}

function checkout() {
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem('soblazn_customer') || '{}');
  } catch (e) {}

  modalBody.innerHTML = `
    <h2>Оформление</h2>
    <div class="form">
      <input id="name" placeholder="Ваше имя"
        value="${saved.name || tg?.initDataUnsafe?.user?.first_name || ''}">
      <input id="phone" placeholder="Телефон" value="${saved.phone || ''}">
      <textarea id="address" placeholder="Адрес доставки">${saved.address || ''}</textarea>
      <textarea id="comment" placeholder="Комментарий к заказу"></textarea>
      <select id="payment">
        <option>Kaspi</option>
        <option>Halyk</option>
        <option>Наличными</option>
      </select>
      <button class="primary" onclick="sendOrder()">Подтвердить заказ</button>
    </div>
  `;
}

async function sendOrder() {
  const customer = {
    name: (document.getElementById('name')?.value || '').trim(),
    phone: (document.getElementById('phone')?.value || '').trim(),
    address: (document.getElementById('address')?.value || '').trim(),
    comment: (document.getElementById('comment')?.value || '').trim(),
    payment: document.getElementById('payment')?.value || 'Kaspi'
  };

  if (!customer.name || !customer.phone || !customer.address) {
    alert('Заполните имя, телефон и адрес');
    return;
  }

  localStorage.setItem('soblazn_customer', JSON.stringify({
    name: customer.name,
    phone: customer.phone,
    address: customer.address
  }));

  try {
    const r = await fetch('/api/order', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        initData: tg?.initData || '',
        cart: cartArray(),
        customer
      })
    });

    const d = await r.json();

    if (d.ok) {
      cart = {};
      updateCart();
      render();
      tg?.HapticFeedback?.notificationOccurred('success');

      localStorage.setItem('soblazn_last_order', JSON.stringify({
        order_number: d.order_number,
        tracking_token: d.tracking_token,
        bot_link: d.bot_link || ''
      }));

      showOrderStatus(d.order_number, d.tracking_token, d.bot_link || '');
    } else {
      alert(d.error || 'Ошибка отправки заказа');
    }
  } catch (e) {
    alert('Не удалось связаться с сервером.');
  }
}

function closeModal() {
  modal.classList.add('hidden');
}

const statusSteps = [
  ['new', '📨', 'Заказ получен'],
  ['admin_accepted', '👨‍🍳', 'Готовится'],
  ['courier_accepted', '🛵', 'Передан курьеру'],
  ['on_way', '🚗', 'Курьер в пути'],
  ['delivered', '✅', 'Доставлен']
];

function statusRank(status) {
  return statusSteps.findIndex(x => x[0] === status);
}

function renderStatusCard(d, orderNumber, token, botLink) {
  const rank = statusRank(d.status);
  const progress = statusSteps.map((step, index) => `
    <div class="trackstep ${index <= rank ? 'done' : ''} ${index === rank ? 'current' : ''}">
      <span>${step[1]}</span><small>${step[2]}</small>
    </div>
  `).join('');

  modalBody.innerHTML = `
    <div class="status-card">
      <h2>Ваш заказ №${orderNumber}</h2>
      <div class="status-main">${d.status_text || 'Загрузка...'}</div>
      <div class="track">${progress}</div>
      ${d.courier ? `<p><b>Курьер:</b> ${d.courier}</p>` : ''}
      ${d.total ? `<p><b>Сумма:</b> ${money(d.total)}</p>` : ''}
      <p class="muted">Статус обновляется автоматически.</p>
      ${botLink ? `
        <a class="primary telegram-link" href="${botLink}">
          📲 Открыть Telegram и получать статусы
        </a>` : ''}
      <button class="secondary" onclick="showMyOrders()">👤 Все мои заказы</button>
      <button class="secondary" onclick="closeModal()">Свернуть</button>
    </div>
  `;

  modal.classList.remove('hidden');

  if (d.status === 'delivered' && statusTimer) {
    clearInterval(statusTimer);
    statusTimer = null;
    localStorage.removeItem('soblazn_last_order');
  }
}

async function fetchOrderStatus(orderNumber, token, botLink) {
  try {
    const r = await fetch(
      `/api/order_status/${encodeURIComponent(orderNumber)}?token=${encodeURIComponent(token)}`
    );
    const d = await r.json();
    if (d.ok) renderStatusCard(d, orderNumber, token, botLink);
  } catch (e) {}
}

function showOrderStatus(orderNumber, token, botLink) {
  if (statusTimer) clearInterval(statusTimer);
  fetchOrderStatus(orderNumber, token, botLink);
  statusTimer = setInterval(
    () => fetchOrderStatus(orderNumber, token, botLink),
    4000
  );
}

function formatOrderDate(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('ru-RU');
}

async function showMyOrders() {
  let customer = {};
  try {
    customer = JSON.parse(localStorage.getItem('soblazn_customer') || '{}');
  } catch (e) {}

  let phone = customer.phone || '';

  if (!tg?.initData && !phone) {
    phone = prompt('Введите номер телефона, который использовали при заказе') || '';
    if (!phone.trim()) return;
  }

  modalBody.innerHTML = '<h2>Мои заказы</h2><p>Загрузка истории...</p>';
  modal.classList.remove('hidden');

  try {
    const r = await fetch('/api/my_orders', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        initData: tg?.initData || '',
        phone
      })
    });

    const d = await r.json();

    if (!d.ok) {
      modalBody.innerHTML = `<h2>Мои заказы</h2><p>${d.error || 'Не удалось загрузить заказы'}</p>`;
      return;
    }

    if (!d.orders.length) {
      modalBody.innerHTML = `
        <h2>Мои заказы</h2>
        <p>История заказов пока пустая.</p>
        <button class="primary" onclick="closeModal()">Перейти к меню</button>
      `;
      return;
    }

    modalBody.innerHTML = `
      <h2>Мои заказы</h2>
      ${d.orders.map(order => `
        <div class="order-history-card">
          <div>
            <b>Заказ №${order.order_number}</b>
            <small>${formatOrderDate(order.created_at)}</small>
          </div>
          <p>${order.status_text}</p>
          <div class="order-items">
            ${(order.items || []).map(item => `
              <div>
                <span>${item.name} × ${item.qty}</span>
                <b>${money(item.total)}</b>
              </div>
            `).join('')}
          </div>
          <div class="order-history-total">
            <span>Итого</span><b>${money(order.total)}</b>
          </div>
          <button class="primary"
            onclick='repeatOrder(${JSON.stringify(order.cart || [])})'>
            🔁 Повторить заказ
          </button>
          ${order.tracking_token && order.status !== 'delivered' ? `
            <button class="secondary"
              onclick="showOrderStatus('${order.order_number}','${order.tracking_token}','')">
              📦 Отследить заказ
            </button>
          ` : ''}
        </div>
      `).join('')}
    `;
  } catch (e) {
    modalBody.innerHTML = '<h2>Мои заказы</h2><p>Не удалось связаться с сервером.</p>';
  }
}

function repeatOrder(savedCart) {
  cart = {};

  for (const row of savedCart || []) {
    const product = menu.find(item => Number(item.id) === Number(row.id));
    if (product && Number(row.qty) > 0) {
      cart[row.id] = Number(row.qty);
    }
  }

  render();
  updateCart();
  tg?.HapticFeedback?.impactOccurred('medium');

  if (!cartArray().length) {
    alert('Некоторые блюда из этого заказа больше недоступны.');
    closeModal();
    return;
  }

  openCart();
}

window.addEventListener('load', () => {
  try {
    const lastOrder = JSON.parse(
      localStorage.getItem('soblazn_last_order') || 'null'
    );

    if (lastOrder?.order_number && lastOrder?.tracking_token) {
      showOrderStatus(
        lastOrder.order_number,
        lastOrder.tracking_token,
        lastOrder.bot_link || ''
      );
    }
  } catch (e) {}
});
