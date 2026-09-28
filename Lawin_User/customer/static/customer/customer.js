(() => {
  'use strict';
  const cfg = JSON.parse(document.getElementById('cfg').textContent);
  const $ = (s) => document.querySelector(s);
  const nf = new Intl.NumberFormat('fa-IR');
  const money = (n) => nf.format(n) + ' تومان';
  const fa = (n) => nf.format(n);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const STATUS = {new:'ثبت شد', preparing:'در حال آماده‌سازی', ready:'آماده شد', delivered:'تحویل داده شد', paid:'پرداخت شد', cancelled:'لغو شد'};
  const STEPS = ['new', 'preparing', 'ready', 'delivered'];
  const CART_KEY = 'cart:' + cfg.table;

  const st = { cats: [], cat: 'all', cart: {}, orders: [], call: null, view: 'menu', ws: null, wsOk: false, retry: 0 };
  try { st.cart = JSON.parse(sessionStorage.getItem(CART_KEY) || '{}'); } catch (e) { st.cart = {}; }
  const saveCart = () => { try { sessionStorage.setItem(CART_KEY, JSON.stringify(st.cart)); } catch (e) {} };

  const csrf = () => (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';
  async function api(url, opt = {}) {
    const r = await fetch(url, { credentials: 'same-origin', ...opt,
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() } });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(data?.error?.message || 'خطایی رخ داد. دوباره تلاش کنید.'), { code: data?.error?.code });
    return data;
  }
  function toast(msg, err) {
    const el = document.createElement('div');
    el.className = 'toast' + (err ? ' err' : ''); el.textContent = msg;
    $('#toasts').append(el); setTimeout(() => el.remove(), 3500);
  }

  const products = () => st.cats.flatMap((c) => c.products);
  const byId = (id) => products().find((p) => p.id === +id);
  const cartLines = () => Object.entries(st.cart).map(([id, l]) => ({ p: byId(id), ...l })).filter((l) => l.p && l.p.available);
  const cartTotal = () => cartLines().reduce((s, l) => s + l.p.price * l.qty, 0);   // فقط نمایشی؛ مبلغ واقعی را سرور حساب می‌کند
  const cartCount = () => cartLines().reduce((s, l) => s + l.qty, 0);

  /* ---------- منو ---------- */
  function showState(text, err) {
    const s = $('#state'); s.hidden = !text; s.textContent = text || ''; s.className = 'state' + (err ? ' err' : '');
  }
  function renderMenu() {
    $('#chips').innerHTML = [{ id: 'all', name: 'همه' }, ...st.cats].map((c) =>
      `<button role="tab" data-cat="${c.id}" class="${String(c.id) === String(st.cat) ? 'on' : ''}">${esc(c.name)}</button>`).join('');
    const list = st.cat === 'all' ? products() : (st.cats.find((c) => String(c.id) === String(st.cat))?.products || []);
    $('#products').innerHTML = list.map((p) => {
      const q = st.cart[p.id]?.qty || 0;
      return `<button class="p ${p.available ? '' : 'off'}" data-p="${p.id}">
        <span class="img">${p.image_url ? `<img loading="lazy" decoding="async" src="${esc(p.image_url)}" alt="">` : '☕'}${q ? `<span class="qty-badge">${fa(q)}</span>` : ''}</span>
        <span class="b"><h3>${esc(p.name)}</h3><span class="d">${esc(p.description)}</span>
        <span class="f"><span class="price">${money(p.price)}</span>
        ${p.available ? '<span class="add">افزودن</span>' : '<span class="add">فعلاً موجود نیست</span>'}</span></span></button>`;
    }).join('');
    showState(list.length ? '' : 'در حال حاضر محصولی در این دسته موجود نیست.');
    renderCartBar();
  }
  function renderCartBar() {
    const b = $('#cartBar'), n = cartCount();
    b.hidden = !n || st.view !== 'menu';
    b.innerHTML = `<span>مشاهده سفارش (${fa(n)} مورد)</span><span>${money(cartTotal())}</span>`;
  }
  function bump() { const b = $('#cartBar'); b.classList.remove('bump'); void b.offsetWidth; b.classList.add('bump'); }

  /* ---------- Sheet ---------- */
  function openSheet(html) { $('#sheet').innerHTML = html; $('#sheet').hidden = false; $('#scrim').hidden = false; }
  function closeSheet() { $('#sheet').hidden = true; $('#scrim').hidden = true; }

  function openProduct(id) {
    const p = byId(id); if (!p) return;
    if (!p.available) { toast('«' + p.name + '» فعلاً موجود نیست.', true); return; }
    let qty = st.cart[id]?.qty || 1;
    openSheet(`${p.image_url ? `<img class="hero" src="${esc(p.image_url)}" alt="">` : '<div class="hero">☕</div>'}
      <h2>${esc(p.name)}</h2><p class="d" style="color:var(--mute);margin:0">${esc(p.description)}</p>
      <p><b>${money(p.price)}</b></p>
      <div class="row" style="border:0"><span class="n">تعداد</span><span class="step"><button data-d="-1" aria-label="کم کردن">−</button><b id="q">${fa(qty)}</b><button data-d="1" aria-label="اضافه کردن">+</button></span></div>
      <input type="text" id="note" maxlength="120" placeholder="توضیح سفارش (مثلاً شکر کمتر)" value="${esc(st.cart[id]?.note || '')}">
      <button class="btn primary" id="addBtn" style="width:100%">افزودن به سفارش</button>`);
    $('#sheet').onclick = (e) => {
      const d = e.target.dataset.d;
      if (d) { qty = Math.min(20, Math.max(1, qty + +d)); $('#q').textContent = fa(qty); }
      if (e.target.id === 'addBtn') {
        st.cart[id] = { qty, note: $('#note').value.trim().slice(0, 120) }; saveCart();
        closeSheet(); renderMenu(); bump();
      }
    };
  }

  function openCart(confirm) {
    const lines = cartLines();
    if (!lines.length) { closeSheet(); return renderMenu(); }
    const list = lines.map((l) => `<div class="row" data-id="${l.p.id}">
      <span class="n">${esc(l.p.name)}${l.note ? `<small>${esc(l.note)}</small>` : ''}<small>${money(l.p.price * l.qty)}</small></span>
      ${confirm ? `<b>× ${fa(l.qty)}</b>` : `<span class="step"><button data-d="-1" aria-label="کم کردن">−</button><b>${fa(l.qty)}</b><button data-d="1" aria-label="اضافه کردن">+</button></span><button data-rm="1" aria-label="حذف" style="border:0;background:none;color:var(--bad)">✕</button>`}
    </div>`).join('');
    openSheet(`<h2>${confirm ? 'آیا سفارش خود را ثبت می‌کنید؟' : 'سبد سفارش'}</h2>
      ${confirm ? `<p style="margin:0;color:var(--mute)">میز ${fa(cfg.table)}</p>` : ''}${list}
      <div class="total"><span>جمع کل</span><span>${money(cartTotal())}</span></div>
      <p id="err" class="state err" hidden></p>
      ${confirm ? `<div class="actions"><button class="btn" id="back">بازگشت</button><button class="btn primary" id="submit">ثبت نهایی سفارش</button></div>`
                : `<button class="btn primary" id="next" style="width:100%">ثبت سفارش</button>`}
      <p class="pay-note">پرداخت پس از تحویل و به‌صورت حضوری انجام می‌شود.</p>`);
    $('#sheet').onclick = async (e) => {
      const t = e.target, row = t.closest('[data-id]');
      if (t.dataset.d && row) {
        const l = st.cart[row.dataset.id]; l.qty = Math.min(20, Math.max(1, l.qty + +t.dataset.d)); saveCart(); openCart(false); renderMenu();
      } else if (t.dataset.rm && row) { delete st.cart[row.dataset.id]; saveCart(); openCart(false); renderMenu(); }
      else if (t.id === 'next') openCart(true);
      else if (t.id === 'back') openCart(false);
      else if (t.id === 'submit') await submit(t);
    };
  }

  async function submit(btn) {
    btn.disabled = true; btn.textContent = 'در حال ثبت…';
    try {
      // فقط شناسه، تعداد و توضیح ارسال می‌شود؛ قیمت‌ها را سرور از دیتابیس می‌خواند.
      const items = cartLines().map((l) => ({ product_id: l.p.id, quantity: l.qty, note: l.note || '' }));
      const order = await api(cfg.urls.orders, { method: 'POST', body: JSON.stringify({ items }) });
      st.cart = {}; saveCart(); upsertOrder(order);
      openSheet(`<div class="ok-pop">✅</div><h2 style="text-align:center">سفارش ${fa(order.number)} ثبت شد</h2>
        <p class="pay-note">وضعیت سفارش را در بخش «سفارش‌های من» دنبال کنید.</p>
        <button class="btn primary" id="seeOrders" style="width:100%">مشاهده وضعیت سفارش</button>`);
      $('#sheet').onclick = (e) => { if (e.target.id === 'seeOrders') { closeSheet(); setView('orders'); } };
      renderMenu(); loadMenu(true);
    } catch (e) {
      btn.disabled = false; btn.textContent = 'ثبت نهایی سفارش';
      const el = $('#err'); el.hidden = false; el.textContent = e.message;
      if (e.code === 'insufficient_stock' || e.code === 'inactive_product') loadMenu(true);
    }
  }

  /* ---------- سفارش‌ها ---------- */
  function upsertOrder(o) {
    const i = st.orders.findIndex((x) => x.id === o.id);
    if (i >= 0) st.orders[i] = o; else st.orders.unshift(o);
    renderOrders();
  }
  function renderOrders() {
    const active = st.orders.filter((o) => !['paid', 'cancelled'].includes(o.status)).length;
    const badge = $('#ordersBadge'); badge.hidden = !active; badge.textContent = fa(active);
    if (!st.orders.length) { $('#orders').innerHTML = '<p class="state">هنوز سفارشی ثبت نکرده‌اید.</p>'; return; }
    $('#orders').innerHTML = st.orders.map((o) => {
      const idx = o.status === 'paid' ? STEPS.length : STEPS.indexOf(o.status);
      const tl = o.status === 'cancelled' ? '<div class="cancelled">این سفارش لغو شده است.</div>' :
        `<ol class="tl" style="padding:0;list-style:none">${STEPS.map((s, i) =>
          `<li class="${i <= idx ? 'done' : ''} ${i === idx ? 'cur' : ''}">${STATUS[s]}</li>`).join('')}</ol>`;
      return `<article class="o"><header><span>سفارش #${fa(o.number)}</span><span>میز ${fa(o.table.number)}</span></header>
        <ul>${o.items.map((i) => `<li>${esc(i.product_name)} × ${fa(i.quantity)}${i.note ? ` — ${esc(i.note)}` : ''}</li>`).join('')}</ul>
        ${tl}<div class="total" style="padding-bottom:0"><span>جمع کل</span><span>${money(o.total)}</span></div></article>`;
    }).join('') + '<p class="pay-note">پرداخت پس از تحویل و به‌صورت حضوری انجام می‌شود.</p>';
  }

  /* ---------- گارسون ---------- */
  let doneTimer;
  function renderWaiter() {
    const c = st.call, note = $('#waiterNote'), bell = $('#bell');
    const active = c && c.status !== 'completed';
    bell.disabled = !!active; bell.classList.toggle('wait', !!active);
    note.hidden = !c; note.classList.toggle('done', c?.status === 'completed');
    if (c) note.textContent = { pending: `درخواست شما برای میز ${fa(cfg.table)} ارسال شد.`,
      acknowledged: 'گارسون در جریان درخواست شما قرار گرفت.', completed: 'درخواست شما انجام شد.' }[c.status];
  }
  function setCall(c) {
    st.call = c; renderWaiter(); clearTimeout(doneTimer);
    if (c?.status === 'completed') doneTimer = setTimeout(() => { st.call = null; renderWaiter(); }, 6000);
  }
  async function callWaiter() {
    $('#bell').disabled = true;
    try {
      const r = await api(cfg.urls.waiter, { method: 'POST', body: '{}' });
      setCall(r.call); toast(r.message);
    } catch (e) { toast(e.message, true); renderWaiter(); }
  }

  /* ---------- WebSocket + بازیابی ---------- */
  function setConn(on) { st.wsOk = on; $('#conn').classList.toggle('on', on); }
  function connect() {
    const url = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + cfg.wsPath;
    const ws = st.ws = new WebSocket(url);
    ws.onopen = () => { st.retry = 0; setConn(true); loadOrders(); loadWaiter(); loadMenu(true); };  // همگام‌سازی بعد از هر اتصال
    ws.onmessage = (m) => {
      const { event, data } = JSON.parse(m.data);
      if (event === 'order_status_changed') {
        upsertOrder(data.order);
        if (data.order.status !== data.previous_status) toast(`سفارش ${fa(data.order.number)}: ${STATUS[data.order.status]}`);
      } else if (event.startsWith('waiter_call_')) setCall(data.call);
    };
    ws.onclose = () => { setConn(false); setTimeout(connect, Math.min(15000, 1000 * 2 ** st.retry++)); };
    ws.onerror = () => ws.close();
  }
  setInterval(() => { if (!st.wsOk) { loadOrders(); loadWaiter(); } }, 15000);   // پشتیبان وقتی WebSocket قطع است

  /* ---------- بارگذاری ---------- */
  async function loadMenu(silent) {
    if (!silent) showState('در حال بارگذاری منو…');
    try {
      st.cats = (await api(cfg.urls.menu)).categories; renderMenu();
      if (!st.cats.length) showState('منو هنوز آماده نیست.');
    } catch (e) { if (!silent) showState(e.message, true); }
  }
  async function loadOrders() { try { st.orders = (await api(cfg.urls.orders)).orders; renderOrders(); } catch (e) {} }
  async function loadWaiter() { try { const r = await api(cfg.urls.waiter); if (r.call || st.call?.status !== 'completed') setCall(r.call); } catch (e) {} }

  function setView(v) {
    st.view = v; $('#menuView').hidden = v !== 'menu'; $('#ordersView').hidden = v !== 'orders';
    document.querySelectorAll('.tabs button').forEach((b) => b.classList.toggle('on', b.dataset.view === v));
    renderCartBar();
  }

  /* ---------- رویدادها ---------- */
  document.addEventListener('click', (e) => {
    const t = e.target;
    if (t.closest('.tabs button')) setView(t.closest('.tabs button').dataset.view);
    else if (t.closest('[data-cat]')) { st.cat = t.closest('[data-cat]').dataset.cat; renderMenu(); }
    else if (t.closest('[data-p]')) openProduct(t.closest('[data-p]').dataset.p);
    else if (t.closest('#cartBar')) openCart(false);
    else if (t.closest('#bell')) callWaiter();
    else if (t.id === 'scrim') closeSheet();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeSheet(); });

  loadMenu(); loadOrders(); loadWaiter(); connect();
})();
