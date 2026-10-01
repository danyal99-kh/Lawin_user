(() => {
  'use strict';

  /* ===================== قرارداد Backend (تغییرناپذیر) =====================
     cfg = {cafe, table, wsPath, urls:{menu, orders, waiter}}
     GET  urls.menu    -> {table, categories:[{id,name,products:[{id,name,description,price,image_url,available}]}]}
     GET  urls.orders  -> {orders:[order_dict]}
     POST urls.orders  -> 201 order_dict   | 400/403/404/409/429 {error:{code,message}}
     GET  urls.waiter  -> {call: call_dict|null}
     POST urls.waiter  -> 201/200 {call, created, message}
     WS   cfg.wsPath   -> {event, data, id, ts}
     sessionStorage["cart:"+cfg.table] = {productId:{qty,note}}
     قیمت نهایی همیشه در Backend محاسبه می‌شود.
  ========================================================================== */

  const cfg = JSON.parse(document.getElementById('cfg').textContent);
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  const nf = new Intl.NumberFormat('fa-IR');
  const money = (n) => nf.format(n) + ' تومان';
  const fa = (n) => nf.format(n);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const STATUS = { new: 'ثبت شد', preparing: 'در حال آماده‌سازی', ready: 'آماده شد', delivered: 'تحویل داده شد', paid: 'پرداخت شد', cancelled: 'لغو شد' };
  const STEPS = ['new', 'preparing', 'ready', 'delivered'];
  const STEP_LBL = { new: 'سفارش ثبت شد', preparing: 'در حال آماده‌سازی', ready: 'آماده شد', delivered: 'تحویل داده شد' };
  const MAX_QTY = 20;   // هم‌راستا با orders/services.py MAX_QTY
  const MAX_NOTE = 120; // هم‌راستا با OrderItem.note
  const CART_KEY = 'cart:' + cfg.table;

  /* ---------- آیکون‌ها ---------- */
  const svg = (p, w = 24) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" width="${w}" height="${w}" aria-hidden="true">${p}</svg>`;
  const I = {
    bag: svg('<path d="M5.5 8h13l-1 11.2a2 2 0 0 1-2 1.8H8.5a2 2 0 0 1-2-1.8z"/><path d="M9 8V6.5a3 3 0 0 1 6 0V8"/>', 20),
    chev: svg('<path d="M14 6l-6 6 6 6"/>', 18),
    plus: svg('<path d="M12 5v14M5 12h14"/>', 17),
    minus: svg('<path d="M5 12h14"/>', 17),
    x: svg('<path d="M6 6l12 12M18 6L6 18"/>', 17),
    check: svg('<path d="M4.5 12.5l5 5 10-11"/>', 14),
    tick: svg('<path d="M20 6L9 17l-5-5"/>', 38),
    bell: svg('<path d="M18 9a6 6 0 1 0-12 0c0 5-2 6-2 6h16s-2-1-2-6"/><path d="M13.7 20a2 2 0 0 1-3.4 0"/>', 23),
    clock: svg('<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>', 14),
    table: svg('<path d="M3 9h18M6 9l1.2 10M18 9l-1.2 10M9.5 9V5h5v4"/>', 13),
    cup: svg('<path d="M5 9h11v5a5.5 5.5 0 0 1-11 0z"/><path d="M16 10.5h1.6a2.2 2.2 0 0 1 0 4.4H16"/><path d="M8.5 3.5c-.8 1 .2 1.5-.6 2.5M12 3c-.8 1 .2 1.5-.6 2.5"/>', 34),
    trash: svg('<path d="M5 7h14M10 7V5h4v2M7 7l.9 12a2 2 0 0 0 2 1.9h4.2a2 2 0 0 0 2-1.9L17 7"/>', 15),
    alert: svg('<circle cx="12" cy="12" r="8.5"/><path d="M12 8v4.5M12 15.6v.4"/>', 17),
    info: svg('<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8.2v.3"/>', 17),
    ban: svg('<circle cx="12" cy="12" r="8.5"/><path d="M6 6l12 12"/>', 16),
    receipt: svg('<path d="M6 3.5h12v17l-2.2-1.6-1.9 1.6-1.9-1.6-1.9 1.6L8.2 19 6 20.5z"/><path d="M9.2 8.5h5.6M9.2 12.5h5.6"/>', 28),
    basket: svg('<path d="M4.5 9.5h15l-1.4 8.4a2 2 0 0 1-2 1.6H7.9a2 2 0 0 1-2-1.6z"/><path d="M9 9.5L11.5 4M15 9.5L12.5 4"/>', 28),
    empty: svg('<path d="M5 9h14l-1 10.5a2 2 0 0 1-2 1.8H8a2 2 0 0 1-2-1.8z"/><path d="M8.5 9.2A3.5 3.5 0 0 1 12 6a3.5 3.5 0 0 1 3.5 3.2"/><path d="M10 13.5h4"/>', 28),
    wifi: svg('<path d="M5 12.5a10 10 0 0 1 14 0M8 15.8a6 6 0 0 1 8 0"/><circle cx="12" cy="19.4" r="1.1" fill="currentColor" stroke="none"/>', 13),
  };

  /* ---------- حالت ---------- */
  const st = { cats: [], cat: 'all', cart: {}, orders: [], call: null, view: 'menu', ws: null, wsOk: false, retry: 0, skTimer: null };

  // سبد sessionStorage است و باید تمیز شود: کلید غیرعددی، تعداد خارج از بازه و یادداشت بلند
  // مستقیماً به Backend می‌رفت و 400 validation می‌گرفت.
  function sanitizeCart(raw) {
    const out = {};
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return out;
    for (const [k, v] of Object.entries(raw)) {
      const id = Number(k);
      if (!Number.isInteger(id) || id <= 0 || !v || typeof v !== 'object') continue;
      const q = Math.round(Number(v.qty));
      if (!Number.isFinite(q)) continue;
      out[id] = { qty: Math.min(MAX_QTY, Math.max(1, q)), note: String(v.note ?? '').slice(0, MAX_NOTE) };
    }
    return out;
  }
  try { st.cart = sanitizeCart(JSON.parse(sessionStorage.getItem(CART_KEY) || '{}')); } catch (e) { st.cart = {}; }
  const saveCart = () => { try { sessionStorage.setItem(CART_KEY, JSON.stringify(st.cart)); } catch (e) {} };

  const csrf = () => (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';
  async function api(url, opt = {}) {
    const r = await fetch(url, {
      credentials: 'same-origin',
      ...opt,
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(data?.error?.message || 'خطایی رخ داد. دوباره تلاش کنید.'), { code: data?.error?.code, status: r.status });
    return data;
  }

  /* ---------- Toast ---------- */
  function toast(msg, kind) {
    const el = document.createElement('div');
    const k = kind === true ? 'err' : (kind || 'info');
    el.className = 'toast ' + k;
    el.setAttribute('role', k === 'err' ? 'alert' : 'status');
    el.innerHTML = (k === 'err' ? I.alert : k === 'ok' ? I.check : I.info) + '<span>' + esc(msg) + '</span>';
    $('#toasts').append(el);
    setTimeout(() => {
      el.classList.add('out');
      el.addEventListener('animationend', () => el.remove(), { once: true });
      setTimeout(() => el.remove(), 400);
    }, k === 'err' ? 5200 : 3400);
  }

  /* ---------- ابزارها ---------- */
  const clamp = (n) => Math.min(MAX_QTY, Math.max(1, n));
  const imgOk = (u) => typeof u === 'string' && (u.startsWith('/') || u.startsWith('http://') || u.startsWith('https://') || u.startsWith('data:image/'));
  const ph = (cls = '') => `<span class="ph ${cls}">${I.cup}</span>`;
  const productImg = (p, cls = '') => imgOk(p.image_url)
    ? `<img src="${esc(p.image_url)}" alt="" loading="lazy" decoding="async" data-ph="1">`
    : ph(cls);
  const timeOf = (iso) => { try { return new Date(iso).toLocaleTimeString('fa-IR', { hour: '2-digit', minute: '2-digit' }); } catch (e) { return ''; } };
  const empty = (icon, title, desc) => `<div class="state"><span class="state-ico">${icon}</span><h3>${esc(title)}</h3><p>${esc(desc)}</p></div>`;

  const products = () => st.cats.flatMap((c) => c.products);
  const byId = (id) => products().find((p) => p.id === +id);
  const cartLines = () => Object.entries(st.cart).map(([id, l]) => ({ p: byId(id), ...l })).filter((l) => l.p && l.p.available);
  const cartTotal = () => cartLines().reduce((s, l) => s + l.p.price * l.qty, 0);   // فقط نمایشی؛ مبلغ واقعی را سرور حساب می‌کند
  const cartCount = () => cartLines().reduce((s, l) => s + l.qty, 0);

  /* ===================== منو ===================== */
  function showState(text, err) {
    const s = $('#state');
    s.hidden = !text;
    if (!text) return;
    s.className = 'state' + (err ? ' err' : '');
    s.innerHTML = `<span class="state-ico">${err ? I.alert : I.basket}</span><h3>${esc(err ? 'مشکلی پیش آمد' : 'در حال بارگذاری…')}</h3><p>${esc(text)}</p>`;
  }

  function skeleton() {
    $('#chips .chips-in').innerHTML = '<span class="sk-chip"></span><span class="sk-chip" style="width:72px"></span><span class="sk-chip" style="width:60px"></span>';
    $('#products').className = 'skel';
    $('#products').innerHTML = Array.from({ length: 5 }, () =>
      `<div class="sk-row"><span class="sk-img"></span><span class="sk-b"><span class="sk-l w70"></span><span class="sk-l w45"></span><span class="sk-l" style="width:34%"></span></span></div>`).join('');
  }
  const cancelSkeleton = () => { if (st.skTimer) { clearTimeout(st.skTimer); st.skTimer = null; } };
  // اسکلتون فقط اگر پاسخ کندتر از ۲۵۰ms باشد؛ وگرنه UI چشمک می‌زند.
  const showSkeletonLater = () => { st.skTimer = setTimeout(skeleton, 250); };

  function renderChips() {
    const box = $('#chips .chips-in');
    box.innerHTML = [{ id: 'all', name: 'همه' }, ...st.cats].map((c) => {
      const on = String(c.id) === String(st.cat);
      return `<button role="tab" data-cat="${esc(c.id)}" class="${on ? 'on' : ''}" aria-selected="${on}" tabindex="${on ? '0' : '-1'}">${esc(c.name)}</button>`;
    }).join('');
    const act = box.querySelector('button.on');
    if (act) act.scrollIntoView({ block: 'nearest', inline: 'center' });
  }

  function renderMenu() {
    cancelSkeleton();
    $('#products').className = 'products';
    renderChips();
    const list = st.cat === 'all' ? products() : (st.cats.find((c) => String(c.id) === String(st.cat))?.products || []);
    const box = $('#products');
    if (!list.length) {
      box.innerHTML = '';
      showState(st.cats.length
        ? 'در این دسته فعلاً محصولی موجود نیست. دسته‌ی دیگری را انتخاب کنید.'
        : 'منو هنوز آماده نشده است. لطفاً کمی بعد دوباره تلاش کنید.');
      renderCartBar();
      return;
    }
    box.innerHTML = list.map((p) => {
      const q = st.cart[p.id]?.qty || 0;
      return `<button class="p ${p.available ? '' : 'off'}" data-p="${p.id}" aria-label="${esc(p.name)}${p.available ? '' : ' — فعلاً موجود نیست'}">
        <span class="img">${productImg(p)}${q ? `<span class="qty-badge" data-badge="${p.id}">${fa(q)}</span>` : ''}</span>
        <span class="b">
          <h3>${esc(p.name)}</h3>
          <span class="d">${esc(p.description)}</span>
          <span class="f">
            <span class="price">${fa(p.price)}<small>تومان</small></span>
            ${p.available
              ? `<span class="add" aria-hidden="true">${I.plus}افزودن</span>`
              : '<span class="add" aria-hidden="true">ناموجود</span>'}
          </span>
        </span>
      </button>`;
    }).join('');
    showState('');
    $('#chips').classList.remove('fade');
    void $('#chips').offsetWidth;
    $('#chips').classList.add('fade');
    renderCartBar();
  }

  // به‌روزرسانی سبک بدون بازسازی DOM: نشان تعداد + نوار سبد
  function syncCart() {
    $$('#products [data-p]').forEach((card) => {
      const id = card.dataset.p;
      const q = st.cart[id]?.qty || 0;
      let badge = card.querySelector('[data-badge]');
      if (q && !badge) {
        badge = document.createElement('span');
        badge.className = 'qty-badge';
        badge.dataset.badge = id;
        card.querySelector('.img').append(badge);
      }
      if (badge) {
        if (!q) { badge.remove(); } else { badge.textContent = fa(q); }
      }
      card.querySelector('.add').innerHTML = card.classList.contains('off') ? 'ناموجود' : I.plus + 'افزودن';
    });
    renderCartBar();
  }

  function renderCartBar() {
    const b = $('#cartBar'), n = cartCount();
    b.hidden = !n || st.view !== 'menu';
    document.body.classList.toggle('has-cart', n > 0);
    if (!n) { b.innerHTML = ''; return; }
    b.innerHTML = `<span class="ci">${I.bag}<b>${fa(n)}</b></span>
      <span class="ct"><span>جمع سبد</span><b>${money(cartTotal())}</b></span>
      <span class="go">مشاهده سفارش ${I.chev}</span>`;
  }
  function bump() { const b = $('#cartBar'); if (b.hidden) return; b.classList.remove('bump'); void b.offsetWidth; b.classList.add('bump'); }

  /* ===================== Sheet ===================== */
  let lastFocus = null;
  function openSheet(html) {
    const sheet = $('#sheet'), scrim = $('#scrim');
    lastFocus = document.activeElement;
    sheet.innerHTML = `<div class="grab" aria-hidden="true"><i></i></div><div class="sheet-in">${html}</div>`;
    sheet.hidden = false;
    scrim.hidden = false;
    document.body.classList.add('locked');
    requestAnimationFrame(() => { scrim.classList.add('show'); sheet.classList.add('show'); });
    wireGrab();
    const h = sheet.querySelector('h2');
    if (h) { h.id = 'sheetTitle'; sheet.setAttribute('aria-labelledby', 'sheetTitle'); }
    const focusable = sheet.querySelector('.btn.primary, button:not(.grab), input, textarea');
    if (focusable) focusable.focus({ preventScroll: true });
  }
  function closeSheet() {
    const sheet = $('#sheet'), scrim = $('#scrim');
    if (sheet.hidden) return;
    scrim.classList.remove('show');
    sheet.classList.remove('show');
    document.body.classList.remove('locked');
    const done = () => { sheet.hidden = true; scrim.hidden = true; sheet.innerHTML = ''; sheet.removeAttribute('aria-labelledby'); };
    if (matchMedia('(prefers-reduced-motion:reduce)').matches) done();
    else setTimeout(done, 280);
    sheet.onclick = null;
    if (lastFocus && lastFocus.isConnected) lastFocus.focus({ preventScroll: true });
    lastFocus = null;
  }

  // درگ‌به‌پایین برای بستن
  function wireGrab() {
    const grab = $('#sheet .grab'), sheet = $('#sheet'), inner = $('#sheet .sheet-in');
    if (!grab) return;
    let y0 = null;
    grab.addEventListener('pointerdown', (e) => { if (inner.scrollTop <= 0) { y0 = e.clientY; sheet.style.transition = 'none'; grab.setPointerCapture(e.pointerId); } });
    grab.addEventListener('pointermove', (e) => {
      if (y0 === null) return;
      const dy = Math.max(0, e.clientY - y0);
      sheet.style.transform = `translateY(${dy}px)`;
    });
    const end = (e) => {
      if (y0 === null) return;
      const dy = Math.max(0, e.clientY - y0);
      y0 = null;
      sheet.style.transition = '';
      sheet.style.transform = '';
      if (dy > 90) closeSheet();
    };
    grab.addEventListener('pointerup', end);
    grab.addEventListener('pointercancel', end);
  }

  // نگه‌داشتن فوکوس داخل Bottom Sheet
  $('#sheet').addEventListener('keydown', (e) => {
    if (e.key !== 'Tab') return;
    const f = $$('button:not([disabled]), input, textarea, a[href], [tabindex]:not([tabindex="-1"])', $('#sheet'))
      .filter((el) => el.offsetParent !== null);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });

  function sheetShell(title, body) {
    return `<div class="sheet-h"><h2>${esc(title)}</h2><button class="x" id="sheetX" aria-label="بستن">${I.x}</button></div>${body}`;
  }

  /* ---------- جزئیات محصول ---------- */
  function openProduct(id) {
    const p = byId(id); if (!p) return;
    if (!p.available) { toast(`«${p.name}» فعلاً موجود نیست.`, 'err'); return; }
    const inCart = !!st.cart[id];
    let qty = inCart ? st.cart[id].qty : 1;

    openSheet(`<div class="hero-wrap">${imgOk(p.image_url)
      ? `<img class="hero" src="${esc(p.image_url)}" alt="" data-ph="1">`
      : `<div class="hero-ph">${I.cup}</div>`}</div>
      <h2 class="name">${esc(p.name)}</h2>
      ${p.description ? `<p class="desc">${esc(p.description)}</p>` : ''}
      <div class="pp price">${fa(p.price)}<small>تومان</small></div>
      <div class="qty">
        <span>تعداد</span>
        <span class="step">
          <button data-d="-1" aria-label="کم کردن تعداد" ${qty <= 1 ? 'disabled' : ''}>${I.minus}</button>
          <b id="q" aria-live="polite">${fa(qty)}</b>
          <button data-d="1" aria-label="اضافه کردن تعداد" ${qty >= MAX_QTY ? 'disabled' : ''}>${I.plus}</button>
        </span>
      </div>
      <label class="field"><b>توضیح سفارش</b>
        <input type="text" id="note" maxlength="${MAX_NOTE}" placeholder="مثلاً بدون شکر، شیر بیشتر" value="${esc(st.cart[id]?.note || '')}">
      </label>
      <button class="btn primary btn-block" id="addBtn">${inCart ? 'به‌روزرسانی سبد' : 'افزودن به سفارش'}</button>`);

    $('#sheet').onclick = (e) => {
      const d = e.target.closest('[data-d]')?.dataset.d;
      if (d) {
        qty = clamp(qty + +d);
        $('#q').textContent = fa(qty);
        const [minus, plus] = $$('#sheet .step button');
        if (minus) minus.disabled = qty <= 1;
        if (plus) plus.disabled = qty >= MAX_QTY;
        return;
      }
      if (e.target.closest('#addBtn') || e.target.id === 'addBtn') {
        st.cart[id] = { qty, note: ($('#note').value || '').trim().slice(0, MAX_NOTE) };
        saveCart();
        closeSheet();
        syncCart();
        bump();
        toast(inCart ? `${p.name} به‌روزرسانی شد` : `${p.name} به سبد اضافه شد`, 'ok');
      } else if (e.target.closest('#sheetX')) closeSheet();
    };
  }

  /* ---------- سبد ---------- */
  function cartRowHtml(l, confirm) {
    return `<div class="crow" data-id="${l.p.id}">
      <span class="th">${imgOk(l.p.image_url) ? `<img src="${esc(l.p.image_url)}" alt="" loading="lazy" decoding="async" data-ph="1">` : I.cup}</span>
      <span class="cb">
        <b>${esc(l.p.name)}</b>
        ${l.note ? `<span class="ln">${esc(l.note)}</span>` : ''}
        <span class="amt" data-amt="${l.p.id}">${money(l.p.price * l.qty)}</span>
      </span>
      ${confirm
        ? `<b>${fa(l.qty)} ×</b>`
        : `<span class="ce">
            <span class="step sm">
              <button data-d="-1" aria-label="کم کردن تعداد ${esc(l.p.name)}">${I.minus}</button>
              <b data-cq="${l.p.id}" aria-live="polite">${fa(l.qty)}</b>
              <button data-d="1" aria-label="اضافه کردن تعداد ${esc(l.p.name)}">${I.plus}</button>
            </span>
            <button class="rm" data-rm="1" aria-label="حذف ${esc(l.p.name)}">${I.trash}</button>
          </span>`}
    </div>`;
  }

  function openCart(confirm) {
    const lines = cartLines();
    if (!lines.length) { closeSheet(); toast('سبد سفارش شما خالی است.', 'info'); return renderMenu(); }

    const list = lines.map((l) => cartRowHtml(l, confirm)).join('');
    const payNote = `<p class="pay-note">${I.info} پرداخت پس از تحویل و به‌صورت حضوری انجام می‌شود.</p>`;

    const body = confirm
      ? `<div class="confirm-meta">${I.receipt}<div><b>سفارش میز ${fa(cfg.table)}</b><span>${fa(lines.length)} قلم · ${fa(cartCount())} عدد</span></div></div>
         ${list}
         <div class="total"><span class="lbl">جمع کل</span><span id="ctot">${money(cartTotal())}</span></div>
         <p class="err-line" id="err" hidden>${I.alert}<span></span></p>
         <div class="actions">
           <button class="btn" id="back">بازگشت</button>
           <button class="btn primary" id="submit">ثبت نهایی سفارش</button>
         </div>${payNote}`
      : `${list}
         <div class="total"><span class="lbl">جمع کل</span><span id="ctot">${money(cartTotal())}</span></div>
         <button class="btn primary btn-block" id="next" style="margin-top:.6rem">ادامه ثبت سفارش</button>${payNote}`;

    openSheet(sheetShell(confirm ? 'آیا سفارش خود را ثبت می‌کنید؟' : 'سبد سفارش', body));

    $('#sheet').onclick = async (e) => {
      const t = e.target;
      const row = t.closest('[data-id]');
      if (t.closest('#sheetX') || t.id === 'sheetX') return closeSheet();

      if (t.closest('[data-d]') && row) {
        const key = row.dataset.id;
        const l = st.cart[key]; if (!l) return;
        l.qty = clamp(l.qty + +t.closest('[data-d]').dataset.d);
        saveCart();
        // به‌روزرسانی هدفمند تا اسکرول و فوکوس از دست نرود
        const p = byId(key);
        row.querySelector('[data-cq]').textContent = fa(l.qty);
        row.querySelector('[data-amt]').textContent = money(p.price * l.qty);
        $('#ctot').textContent = money(cartTotal());
        const minus = row.querySelector('[data-d="-1"]');
        if (minus) minus.disabled = l.qty <= 1;
        const plus = row.querySelector('[data-d="1"]');
        if (plus) plus.disabled = l.qty >= MAX_QTY;
        syncCart();
        return;
      }
      if (t.closest('[data-rm]') && row) {
        const key = row.dataset.id;
        const name = byId(key)?.name || '';
        delete st.cart[key];
        saveCart();
        row.remove();
        toast(`${name} از سبد حذف شد`, 'info');
        if (!cartLines().length) { closeSheet(); renderMenu(); return; }
        $('#ctot').textContent = money(cartTotal());
        syncCart();
        return;
      }
      if (t.closest('#next')) return openCart(true);
      if (t.closest('#back')) return openCart(false);
      if (t.closest('#submit')) return submit(t.closest('#submit'));
    };
  }

  /* ---------- ثبت سفارش ---------- */
  async function submit(btn) {
    const err = $('#err');
    btn.disabled = true;
    btn.textContent = 'در حال ثبت…';
    if (err) err.hidden = true;
    try {
      // فقط شناسه، تعداد و توضیح ارسال می‌شود؛ قیمت‌ها را سرور از دیتابیس می‌خواند.
      const items = cartLines().map((l) => ({ product_id: l.p.id, quantity: l.qty, note: l.note || '' }));
      const order = await api(cfg.urls.orders, { method: 'POST', body: JSON.stringify({ items }) });
      st.cart = {}; saveCart();
      upsertOrder(order);
      openSuccess(order);
      syncCart();
      loadMenu(true);
    } catch (e) {
      btn.disabled = false; btn.textContent = 'ثبت نهایی سفارش';
      if (err) {
        err.hidden = false;
        err.querySelector('span').textContent = e.message;
      } else toast(e.message, 'err');
      // موجودی یا فعالیت محصول تغییر کرده: منو باید دوباره خوانده شود.
      if (e.code === 'insufficient_stock' || e.code === 'inactive_product') {
        toast('موجودی این محصول تغییر کرده است. منو به‌روزرسانی شد.', 'err');
        loadMenu(true);
      }
    }
  }

  function openSuccess(order) {
    openSheet(`<div class="ok">
      <div class="ok-ico">${I.tick}</div>
      <h2>سفارش شما ثبت شد</h2>
      <div class="ok-num">سفارش #${fa(order.number)}<span>${timeOf(order.created_at)}</span></div>
      <p>آشپزخانه سفارش شما را دریافت کرد. وضعیت را می‌توانید در بخش «سفارش‌های من» دنبال کنید.</p>
      <button class="btn primary btn-block" id="seeOrders" style="margin-top:1.1rem">مشاهده وضعیت سفارش</button>
    </div>`);
    $('#sheet').onclick = (e) => { if (e.target.closest('#seeOrders')) { closeSheet(); setView('orders'); } };
  }

  /* ===================== سفارش‌ها ===================== */
  function upsertOrder(o) {
    const i = st.orders.findIndex((x) => x.id === o.id);
    if (i >= 0) st.orders[i] = o; else st.orders.unshift(o);
    renderOrders();
  }

  function timelineHtml(o) {
    if (o.status === 'cancelled') return `<div class="cancelled">${I.ban}<span>این سفارش لغو شده است.</span></div>`;
    const idx = o.status === 'paid' ? STEPS.length : STEPS.indexOf(o.status);
    return `<ol class="tl">${STEPS.map((s, i) => {
      const cls = i < idx ? 'done' : i === idx ? 'done cur' : '';
      return `<li class="${cls}"><span class="dot">${I.check}</span><span class="tx">${esc(STEP_LBL[s])}</span></li>`;
    }).join('')}</ol>`;
  }

  function renderOrders() {
    const badge = $('#ordersBadge');
    const active = st.orders.filter((o) => !['paid', 'cancelled'].includes(o.status)).length;
    badge.hidden = !active;
    badge.textContent = fa(active);

    const box = $('#orders');
    if (!st.orders.length) {
      box.innerHTML = empty(I.receipt, 'هنوز سفارشی ثبت نکرده‌اید', 'از بخش «منو» محصولات را انتخاب کنید و اولین سفارش خود را ثبت کنید.');
      return;
    }
    box.innerHTML = st.orders.map((o) => {
      const stt = STATUS[o.status] || o.status;
      const items = (o.items || []).map((i) =>
        `<li><span>${esc(i.product_name)}${i.note ? `<span class="note">${esc(i.note)}</span>` : ''}</span><span class="q">${fa(i.quantity)} ×</span></li>`).join('');
      return `<article class="o">
        <div class="o-h">
          <span class="no">سفارش #${fa(o.number)}</span>
          <span class="tb">${I.table} میز ${fa(o.table.number)}</span>
          <span class="sp"></span>
          <span class="badge ${esc(o.status)}"><i></i>${esc(stt)}</span>
        </div>
        <ul class="o-items">${items}</ul>
        ${timelineHtml(o)}
        <div class="o-tot"><span>جمع کل</span><span>${money(o.total)}</span></div>
        <div class="o-foot">
          <span class="pay-note" style="margin:0">${I.clock} ${esc(timeOf(o.created_at))}</span>
          <span class="pay-note" style="margin:0">${I.info} پرداخت در محل</span>
        </div>
      </article>`;
    }).join('') + '<p class="pay-note">پرداخت پس از تحویل و به‌صورت حضوری انجام می‌شود.</p>';
  }

  /* ===================== گارسون ===================== */
  let doneTimer;
  const CALL_TXT = {
    pending: `درخواست شما برای میز ${fa(cfg.table)} ارسال شد.`,
    acknowledged: 'گارسون در جریان درخواست شما قرار گرفت.',
    completed: 'درخواست شما انجام شد.',
  };
  function renderWaiter() {
    const c = st.call, note = $('#waiterNote'), bell = $('#bell');
    const active = c && c.status !== 'completed';
    bell.disabled = !!active;
    bell.classList.toggle('pending', c?.status === 'pending');
    bell.classList.toggle('ack', c?.status === 'acknowledged');
    bell.classList.toggle('done', c?.status === 'completed');
    const label = { pending: 'ارسال شد', acknowledged: 'در جریان', completed: 'انجام شد' }[c?.status] || 'گارسون';
    bell.querySelector('small').textContent = label;
    bell.setAttribute('aria-label', active ? 'درخواست گارسون ثبت شده است' : 'صدا کردن گارسون');

    note.hidden = !c;
    if (!c) return;
    note.className = 'waiter-note' + (c.status === 'acknowledged' ? ' ack' : c.status === 'completed' ? ' done' : '');
    const icon = c.status === 'completed' ? I.check : I.bell;
    note.innerHTML = `<span class="wi">${icon}</span><span>${esc(CALL_TXT[c.status] || '')}</span>`;
  }
  function setCall(c) {
    const was = st.call?.status;
    st.call = c;
    renderWaiter();
    clearTimeout(doneTimer);
    if (c && c.status !== was && c.status !== 'pending') {
      $('#bell').classList.remove('pop'); void $('#bell').offsetWidth; $('#bell').classList.add('pop');
      if (c.status === 'completed') toast(CALL_TXT.completed, 'ok');
    }
    if (c?.status === 'completed') doneTimer = setTimeout(() => { st.call = null; renderWaiter(); }, 6000);
  }
  async function callWaiter() {
    $('#bell').disabled = true;
    try {
      const r = await api(cfg.urls.waiter, { method: 'POST', body: '{}' });
      setCall(r.call);
      toast(r.message, 'ok');
    } catch (e) { toast(e.message, 'err'); renderWaiter(); }
  }

  /* ===================== WebSocket + بازیابی ===================== */
  function setConn(on) {
    st.wsOk = on;
    const el = $('#conn');
    el.classList.toggle('on', on);
    el.innerHTML = `<i></i>${I.wifi}<b>${on ? 'متصل' : 'اتصال مجدد…'}</b>`;
    el.title = on ? 'ارتباط زنده فعال است' : 'در حال اتصال دوباره';
  }
  function connect() {
    const url = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + cfg.wsPath;
    let ws;
    try { ws = new WebSocket(url); } catch (e) { setConn(false); setTimeout(connect, 2000); return; }
    const ws_ = st.ws = ws;
    ws.onopen = () => { if (st.ws !== ws_) return; st.retry = 0; setConn(true); loadOrders(); loadWaiter(); loadMenu(true); };  // همگام‌سازی بعد از هر اتصال
    ws.onmessage = (m) => {
      if (st.ws !== ws_) return;
      let msg; try { msg = JSON.parse(m.data); } catch (e) { return; }
      const { event, data } = msg;
      if (event === 'order_status_changed' && data?.order) {
        upsertOrder(data.order);
        if (data.order.status !== data.previous_status) toast(`سفارش ${fa(data.order.number)}: ${STATUS[data.order.status] || ''}`, 'ok');
      } else if (typeof event === 'string' && event.startsWith('waiter_call_')) {
        setCall(data?.call ?? null);
      }
    };
    ws.onclose = () => { if (st.ws !== ws_) return; setConn(false); setTimeout(connect, Math.min(15000, 1000 * 2 ** st.retry++)); };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
  }
  // پشتیبان وقتی WebSocket قطع است
  setInterval(() => { if (!st.wsOk) { loadOrders(); loadWaiter(); } }, 15000);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && !st.wsOk) { loadOrders(); loadWaiter(); loadMenu(true); }
  });

  /* ===================== بارگذاری ===================== */
  async function loadMenu(silent) {
    if (!silent) { showState('در حال دریافت منو…'); showSkeletonLater(); }
    try {
      st.cats = (await api(cfg.urls.menu)).categories || [];
      // حذف اقلام سبدی که دیگر در منو نیستند (سبد پس از ۵ ثانیه از sessionStorage پاک می‌شود)
      const ids = new Set(products().map((p) => p.id));
      let changed = false;
      for (const k of Object.keys(st.cart)) if (!ids.has(+k)) { delete st.cart[k]; changed = true; }
      if (changed) saveCart();
      if (!st.cats.length) {
        cancelSkeleton();
        $('#products').className = 'products';
        $('#products').innerHTML = '';
        renderChips();
        showState('منو هنوز آماده نشده است. لطفاً کمی بعد دوباره تلاش کنید.');
      } else renderMenu();
    } catch (e) {
      if (silent) return;
      cancelSkeleton();
      $('#products').className = 'products';
      $('#products').innerHTML = '';
      $('#chips .chips-in').innerHTML = '';
      showState(e.message || 'ارتباط با سرور برقرار نشد.', true);
    }
  }
  async function loadOrders() { try { st.orders = (await api(cfg.urls.orders)).orders || []; renderOrders(); } catch (e) { if (!st.orders.length) renderOrders(); } }
  async function loadWaiter() { try { const r = await api(cfg.urls.waiter); if (r.call || st.call?.status !== 'completed') setCall(r.call ?? null); } catch (e) {} }

  /* ===================== نماها ===================== */
  function setView(v) {
    st.view = v;
    $('#menuView').hidden = v !== 'menu';
    $('#ordersView').hidden = v !== 'orders';
    $$('.tabs button').forEach((b) => {
      const on = b.dataset.view === v;
      b.classList.toggle('on', on);
      b.setAttribute('aria-selected', String(on));
    });
    renderCartBar();
    window.scrollTo({ top: 0, behavior: 'instant' in document.documentElement.style ? 'instant' : 'auto' });
  }

  /* ===================== رویدادها ===================== */
  document.addEventListener('click', (e) => {
    const t = e.target;
    const tab = t.closest('.tabs button');
    const chip = t.closest('[data-cat]');
    const card = t.closest('[data-p]');
    if (tab) return setView(tab.dataset.view);
    if (chip) { st.cat = chip.dataset.cat; renderMenu(); return; }
    if (card) return openProduct(card.dataset.p);
    if (t.closest('#cartBar')) return openCart(false);
    if (t.closest('#bell')) return callWaiter();
    if (t.id === 'scrim') return closeSheet();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('#sheet').hidden) { closeSheet(); return; }
    // ناوبری با کیبورد در دسته‌بندی‌ها
    if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && document.activeElement?.dataset?.cat) {
      const chips = $$('#chips button');
      const i = chips.indexOf(document.activeElement);
      const j = (i + (e.key === 'ArrowLeft' ? 1 : -1) + chips.length) % chips.length;
      if (chips[j]) { chips[j].focus(); st.cat = chips[j].dataset.cat; renderMenu(); $$('#chips button')[j]?.focus(); }
    }
  });

  // تصویر خراب نباید layout را بشکند
  document.addEventListener('error', (e) => {
    const el = e.target;
    if (el.tagName !== 'IMG' || !el.dataset.ph) return;
    const box = document.createElement('span');
    box.className = 'ph';
    box.innerHTML = I.cup;
    el.replaceWith(box);
  }, true);

  // اندازه‌گیری ارتفاع هدر برای چسبیدن درست دسته‌بندی‌ها
  const head = $('.top');
  const measure = () => document.documentElement.style.setProperty('--head-h', head.offsetHeight + 'px');
  if ('ResizeObserver' in window) new ResizeObserver(measure).observe(head);
  window.addEventListener('resize', measure);
  window.addEventListener('orientationchange', () => setTimeout(measure, 200));
  measure();

  /* ===================== راه‌اندازی ===================== */
  setConn(false);
  renderWaiter();
  renderCartBar();
  setView('menu');
  loadMenu();
  loadOrders();
  loadWaiter();
  connect();
})();