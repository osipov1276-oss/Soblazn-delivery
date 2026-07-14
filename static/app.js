const tg=window.Telegram?.WebApp; if(tg){tg.ready();tg.expand();}
let menu=[],cart={},active='Все',statusTimer=null; const icons={'🍳':'🍳','🍲':'🍲','🍝':'🍝','🥗':'🥗','🍖':'🍖','🍕':'🍕','🥟':'🥟','🍢':'🍢','🍟':'🍟','🍰':'🍰','🥤':'🥤'};
const money=n=>new Intl.NumberFormat('ru-RU').format(n)+' ₸';
fetch('/api/menu').then(r=>r.json()).then(d=>{menu=d.items;renderCats();render();updateCart()});
function categories(){return ['Все',...new Set(menu.map(x=>x.category))]}
function renderCats(){cats.innerHTML=categories().map(c=>`<button class="${c===active?'active':''}" onclick="active=${JSON.stringify(c)};renderCats();render()">${c}</button>`).join('')}
function emoji(cat){return [...cat][0]||'🍽'}
function render(){let q=search.value.toLowerCase();let list=menu.filter(x=>(active==='Все'||x.category===active)&&x.name.toLowerCase().includes(q));products.innerHTML=list.map(p=>{let n=cart[p.id]||0;return `<article class="product"><div class="thumb">${emoji(p.category)}</div><h3>${p.name}</h3><div class="price">${money(p.price)}</div>${n?`<div class="qty"><button onclick="change(${p.id},-1)">−</button><b>${n}</b><button onclick="change(${p.id},1)">+</button></div>`:`<button class="add" onclick="change(${p.id},1)">Добавить</button>`}</article>`}).join('')||'<p>Ничего не найдено</p>'}
search.oninput=render; cartTop.onclick=openCart;
function change(id,d){cart[id]=Math.max(0,(cart[id]||0)+d);if(!cart[id])delete cart[id];render();updateCart()}
function cartArray(){return Object.entries(cart).map(([id,qty])=>({id:+id,qty}))}
function updateCart(){let count=Object.values(cart).reduce((a,b)=>a+b,0);let total=Object.entries(cart).reduce((s,[id,q])=>s+menu.find(x=>x.id==id).price*q,0);cartCount.textContent=count;barCount.textContent=count+' поз.';barTotal.textContent=money(total);cartBar.classList.toggle('hidden',!count)}
async function openCart(){if(!cartArray().length)return;let calc=await fetch('/api/calculate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cart:cartArray()})}).then(r=>r.json());modalBody.innerHTML=`<h2>Корзина</h2>${calc.items.map(x=>`<div class="cartrow"><div><b>${x.name}</b><br><small>${money(x.price)} × ${x.qty}</small></div><div><button onclick="change(${x.id},-1);openCart()">−</button> ${x.qty} <button onclick="change(${x.id},1);openCart()">+</button></div></div>`).join('')}<div class="totals"><div><span>Блюда</span><b>${money(calc.subtotal)}</b></div><div><span>Упаковка</span><b>${money(calc.packaging_fee)}</b></div><div><span>Доставка</span><b>${money(calc.delivery_fee)}</b></div><div class="grand"><span>Итого</span><b>${money(calc.total)}</b></div></div><button class="primary" onclick="checkout()">Оформить заказ</button>`;modal.classList.remove('hidden')}
function checkout(){modalBody.innerHTML=`<h2>Оформление</h2><div class="form"><input id="name" placeholder="Ваше имя" value="${tg?.initDataUnsafe?.user?.first_name||''}"><input id="phone" placeholder="Телефон"><textarea id="address" placeholder="Адрес доставки"></textarea><textarea id="comment" placeholder="Комментарий к заказу"></textarea><select id="payment"><option>Kaspi</option><option>Halyk</option><option>Наличными</option></select><button class="primary" onclick="sendOrder()">Подтвердить заказ</button></div>`}
async function sendOrder(){
  const nameInput=document.getElementById('name');
  const phoneInput=document.getElementById('phone');
  const addressInput=document.getElementById('address');
  const commentInput=document.getElementById('comment');
  const paymentInput=document.getElementById('payment');
  const customer={
    name:(nameInput?.value||'').trim(),
    phone:(phoneInput?.value||'').trim(),
    address:(addressInput?.value||'').trim(),
    comment:(commentInput?.value||'').trim(),
    payment:paymentInput?.value||'Kaspi'
  };
  if(!customer.name||!customer.phone||!customer.address){alert('Заполните имя, телефон и адрес');return}
  const body={initData:tg?.initData||'',cart:cartArray(),customer};
  try{
    const r=await fetch('/api/order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d=await r.json();
    if(d.ok){
      cart={};updateCart();render();tg?.HapticFeedback?.notificationOccurred('success');
      localStorage.setItem('soblazn_last_order',JSON.stringify({order_number:d.order_number,tracking_token:d.tracking_token,bot_link:d.bot_link||''}));
      showOrderStatus(d.order_number,d.tracking_token,d.bot_link||'');
    }
    else alert(d.error||'Ошибка отправки заказа');
  }catch(e){alert('Не удалось связаться с сервером. Проверьте, что чёрное окно запущено.');}
}
function closeModal(){modal.classList.add('hidden')}
const statusSteps=[
  ['new','📨','Заказ получен'],
  ['admin_accepted','👨‍🍳','Готовится'],
  ['courier_accepted','🛵','Передан курьеру'],
  ['on_way','🚗','Курьер в пути'],
  ['delivered','✅','Доставлен']
];
function statusRank(s){return statusSteps.findIndex(x=>x[0]===s)}
function renderStatusCard(d,orderNumber,token,botLink){
  const rank=statusRank(d.status);
  const progress=statusSteps.map((x,i)=>`<div class="trackstep ${i<=rank?'done':''} ${i===rank?'current':''}"><span>${x[1]}</span><small>${x[2]}</small></div>`).join('');
  modalBody.innerHTML=`<div class="status-card"><h2>Ваш заказ №${orderNumber}</h2><div class="status-main">${d.status_text||'Загрузка...'}</div><div class="track">${progress}</div>${d.courier?`<p><b>Курьер:</b> ${d.courier}</p>`:''}${d.total?`<p><b>Сумма:</b> ${money(d.total)}</p>`:''}<p class="muted">Статус обновляется автоматически.</p>${botLink?`<a class="primary telegram-link" href="${botLink}">📲 Открыть Telegram и получать статусы</a>`:''}<button class="secondary" onclick="closeModal()">Свернуть</button></div>`;
  modal.classList.remove('hidden');
  if(d.status==='delivered' && statusTimer){clearInterval(statusTimer);statusTimer=null;localStorage.removeItem('soblazn_last_order')}
}
async function fetchOrderStatus(orderNumber,token,botLink){
  try{
    const r=await fetch(`/api/order_status/${encodeURIComponent(orderNumber)}?token=${encodeURIComponent(token)}`);
    const d=await r.json();
    if(d.ok)renderStatusCard(d,orderNumber,token,botLink);
  }catch(e){}
}
function showOrderStatus(orderNumber,token,botLink){
  if(statusTimer)clearInterval(statusTimer);
  fetchOrderStatus(orderNumber,token,botLink);
  statusTimer=setInterval(()=>fetchOrderStatus(orderNumber,token,botLink),4000);
}
window.addEventListener('load',()=>{
  try{const x=JSON.parse(localStorage.getItem('soblazn_last_order')||'null');if(x?.order_number&&x?.tracking_token)showOrderStatus(x.order_number,x.tracking_token,x.bot_link||'')}catch(e){}
});
