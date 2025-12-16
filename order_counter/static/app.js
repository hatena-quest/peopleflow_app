'use strict';

window.addEventListener('DOMContentLoaded', () => {
  const MENU = {
    four: { name: '4', price: 350 },
    six: { name: '6', price: 450 },
    eight: { name: '8', price: 550 },
    ten: { name: '10', price: 650 },
    fourteen: { name: '14', price: 1000 },
    takosen: { name: 'たこせん', price: 300 },
    topping: { name: 'トッピング', price: 50 },
  };

  const menuContainer = document.getElementById('menuContainer');
  const todayLabel = document.getElementById('todayLabel');
  const exportButton = document.getElementById('exportButton');
  const resetButton = document.getElementById('resetButton');
  const historyList = document.getElementById('historyList');
  const dailyTotalEl = document.getElementById('dailyTotal');
  const cartTotalEl = document.getElementById('cartTotal');
  const checkoutButton = document.getElementById('checkoutButton');
  const clearCartButton = document.getElementById('clearCartButton');

  const pad = (value, length = 2) => String(value).padStart(length, '0');

  const getLocalDateString = (date = new Date()) => {
    const year = date.getFullYear();
    const month = pad(date.getMonth() + 1);
    const day = pad(date.getDate());
    return `${year}-${month}-${day}`;
  };

  const getLocalDateTimeString = (date = new Date()) => {
    const hours = pad(date.getHours());
    const minutes = pad(date.getMinutes());
    const seconds = pad(date.getSeconds());
    const milliseconds = pad(date.getMilliseconds(), 3);
    return `${getLocalDateString(date)}T${hours}:${minutes}:${seconds}.${milliseconds}`;
  };

  const getTodayString = () => getLocalDateString();
  const getTodayUtcString = () => new Date().toISOString().slice(0, 10);
  const ordersStorageKey = () => `orders_${getTodayString()}`;
  const cartStorageKey = () => `cart_${getTodayString()}`;

  const normalizeTimeString = (value) => {
    if (!value) return getLocalDateTimeString();
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return getLocalDateTimeString();
    }
    return getLocalDateTimeString(date);
  };
  const createId = () => {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
      return crypto.randomUUID();
    }
    return `co_${Date.now()}_${Math.random().toString(16).slice(2)}`;
  };

  const safeParseArray = (raw) => {
    if (!raw) return [];
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (err) {
      console.warn('ストレージのデータが壊れています', err);
      return [];
    }
  };

  const normalizeCheckout = (entry, index) => {
    if (entry && Array.isArray(entry.items)) {
      const items = entry.items.map((item) => ({
        menuId: item.menuId ?? null,
        name: item.name ?? '不明',
        price: Number(item.price) || 0,
        quantity: Number(item.quantity) || 1,
      }));
      const total =
        typeof entry.total === 'number'
          ? entry.total
          : items.reduce((sum, item) => sum + item.price * item.quantity, 0);
      return {
        id: entry.id || createId(),
        time: normalizeTimeString(entry.time),
        items,
        total,
      };
    }

    const price = Number(entry?.price) || 0;
    return {
      id: entry?.id || `legacy_${index}_${Date.now()}`,
      time: normalizeTimeString(entry?.time),
      items: [
        {
          menuId: entry?.menuId ?? null,
          name: entry?.name ?? '不明',
          price,
          quantity: 1,
        },
      ],
      total: price,
    };
  };

  const loadCheckouts = () => {
    const raw = localStorage.getItem(ordersStorageKey());
    const parsed = safeParseArray(raw);
    return parsed.map(normalizeCheckout);
  };

  const saveCheckouts = (checkouts) => {
    localStorage.setItem(ordersStorageKey(), JSON.stringify(checkouts));
  };

  const loadCart = () => {
    const raw = localStorage.getItem(cartStorageKey());
    return safeParseArray(raw);
  };

  const saveCart = (cart) => {
    if (!cart || cart.length === 0) {
      localStorage.removeItem(cartStorageKey());
      return;
    }
    localStorage.setItem(cartStorageKey(), JSON.stringify(cart));
  };

  const aggregateCartItems = (cart) => {
    const map = new Map();
    cart.forEach((item) => {
      const key = `${item.menuId ?? item.name}_${item.price}`;
      if (!map.has(key)) {
        map.set(key, {
          key,
          menuId: item.menuId ?? null,
          name: item.name,
          price: item.price,
          quantity: 0,
        });
      }
      map.get(key).quantity += 1;
    });
    return Array.from(map.values());
  };

  const renderTodayLabel = () => {
    if (!todayLabel) return;
    todayLabel.textContent = `データキー: ${ordersStorageKey()} / カート: ${cartStorageKey()}`;
  };

  const renderHistory = (checkouts) => {
    if (!historyList) return;
    historyList.innerHTML = '';
    if (checkouts.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'history-empty';
      empty.textContent = 'まだ記録がありません';
      historyList.appendChild(empty);
      return;
    }

    const sorted = checkouts
      .map((checkout, index) => ({ ...checkout, originalIndex: index }))
      .sort((a, b) => new Date(b.time) - new Date(a.time));

    sorted.forEach((entry) => {
      const row = document.createElement('div');
      row.className = 'history-row';
      const info = document.createElement('div');
      info.className = 'history-info';
      const itemsText = entry.items
        .map((item) => `${item.name} ×${item.quantity}`)
        .join(' / ');
      const timeText = new Date(entry.time).toLocaleTimeString('ja-JP', {
        hour12: false,
      });
      info.innerHTML = `
        <span>${itemsText}</span>
        <span class="history-total">合計 ¥${entry.total.toLocaleString('ja-JP')}</span>
        <span class="history-time">${timeText}</span>
      `;
      row.appendChild(info);
      historyList.appendChild(row);
    });
  };

  const renderDailyTotal = (checkouts) => {
    if (!dailyTotalEl) return;
    const total = checkouts.reduce((sum, entry) => sum + (entry.total || 0), 0);
    dailyTotalEl.textContent = `合計 ¥${total.toLocaleString('ja-JP')}`;
  };

  const renderCartSummary = (cart) => {
    if (cartTotalEl) {
      const subtotal = cart.reduce((sum, item) => sum + item.price, 0);
      cartTotalEl.textContent = `¥${subtotal.toLocaleString('ja-JP')}`;
    }
    const disabled = cart.length === 0;
    if (checkoutButton) {
      checkoutButton.disabled = disabled;
    }
    if (clearCartButton) {
      clearCartButton.disabled = disabled;
    }
  };

  const refreshCheckouts = (checkouts) => {
    const data = checkouts ?? loadCheckouts();
    renderHistory(data);
    renderDailyTotal(data);
    return data;
  };

  const refreshCart = (cart) => {
    const data = cart ?? loadCart();
    renderCartSummary(data);
    return data;
  };

  const addMenuToCart = (menuId) => {
    const menu = MENU[menuId];
    if (!menu) return;
    const cart = loadCart();
    cart.push({
      menuId,
      name: menu.name,
      price: menu.price,
      addedAt: getLocalDateTimeString(),
    });
    saveCart(cart);
    renderCartSummary(cart);
  };

  const clearCart = () => {
    const cart = loadCart();
    if (cart.length === 0) return;
    if (!confirm('現在の小計をリセットしますか？')) return;
    saveCart([]);
    renderCartSummary([]);
  };

  const checkoutCart = () => {
    const cart = loadCart();
    if (cart.length === 0) {
      alert('小計に商品がありません。');
      return;
    }
    const items = aggregateCartItems(cart).map(({ key, ...rest }) => rest);
    const total = cart.reduce((sum, item) => sum + item.price, 0);
    const checkouts = loadCheckouts();
    checkouts.push({
      id: createId(),
      time: getLocalDateTimeString(),
      items,
      total,
    });
    saveCheckouts(checkouts);
    saveCart([]);
    refreshCheckouts(checkouts);
    renderCartSummary([]);
  };

  const exportToday = () => {
    const checkouts = loadCheckouts();
    if (checkouts.length === 0) {
      alert('本日のデータはまだありません。');
      return;
    }
    const date = getTodayString();
    const fileName = `${ordersStorageKey()}.json`;
    const json = JSON.stringify({ date, checkouts }, null, 2);
    const file = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(file);
    const link = document.createElement('a');
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const resetToday = () => {
    const date = getTodayString();
    const hasCheckouts = loadCheckouts().length > 0;
    const hasCart = loadCart().length > 0;
    if (!hasCheckouts && !hasCart) {
      if (confirm(`${date} のデータはありません。キーを初期化しますか？`)) {
        localStorage.removeItem(ordersStorageKey());
        localStorage.removeItem(cartStorageKey());
      }
      return;
    }
    const ok = confirm(`${date} の全ての会計とカートを削除します。よろしいですか？`);
    if (!ok) return;
    localStorage.removeItem(ordersStorageKey());
    localStorage.removeItem(cartStorageKey());
    refreshCheckouts([]);
    renderCartSummary([]);
  };

  const setupMenuButtons = () => {
    if (!menuContainer) return;
    menuContainer.innerHTML = '';
    Object.entries(MENU).forEach(([menuId, info]) => {
      const button = document.createElement('button');
      button.className = 'menu-button';
      button.innerHTML = `${info.name}<span class="price">¥${info.price.toLocaleString('ja-JP')}</span>`;
      button.addEventListener('click', () => addMenuToCart(menuId));
      menuContainer.appendChild(button);
    });
  };

  const init = () => {
    // 以前の実装(UTC日付キー)で保存したデータがある場合、今日(ローカル日付キー)へ移行
    const migrateStorageIfNeeded = () => {
      const local = getTodayString();
      const utc = getTodayUtcString();
      if (local === utc) return;

      const localOrdersKey = `orders_${local}`;
      const utcOrdersKey = `orders_${utc}`;
      if (!localStorage.getItem(localOrdersKey) && localStorage.getItem(utcOrdersKey)) {
        localStorage.setItem(localOrdersKey, localStorage.getItem(utcOrdersKey));
      }

      const localCartKey = `cart_${local}`;
      const utcCartKey = `cart_${utc}`;
      if (!localStorage.getItem(localCartKey) && localStorage.getItem(utcCartKey)) {
        localStorage.setItem(localCartKey, localStorage.getItem(utcCartKey));
      }
    };

    migrateStorageIfNeeded();
    renderTodayLabel();
    setupMenuButtons();
    refreshCart();
    refreshCheckouts();
    if (exportButton) {
      exportButton.addEventListener('click', exportToday);
    }
    if (resetButton) {
      resetButton.addEventListener('click', resetToday);
    }
    if (checkoutButton) {
      checkoutButton.addEventListener('click', checkoutCart);
    }
    if (clearCartButton) {
      clearCartButton.addEventListener('click', clearCart);
    }

    const streamStart = document.getElementById('streamStart');
    const streamStop = document.getElementById('streamStop');
    const masterStart = document.getElementById('masterStart');
    const masterStop = document.getElementById('masterStop');
    const openStream = document.getElementById('openStream');
    const openMaster = document.getElementById('openMaster');
    const openPredict = document.getElementById('openPredict');
    const svcStatus = document.getElementById('svcStatus');
    const streamStatus = document.getElementById('streamStatus');

    const apiGet = (path) => fetch(path).then((r) => r.json());
    const apiPost = (path) => fetch(path, { method: 'POST' }).then((r) => r.json());

    let cached = null;

    const getBaseHost = () => window.location.hostname; // raspberrypi.local or IP

    const refreshSvc = async () => {
      try {
        const [s, m, u] = await Promise.all([
          apiGet('/api/stream/status'),
          apiGet('/api/master/status'),
          apiGet('/api/urls'),
        ]);
        cached = { s, m, u };

        const streamText = s.running ? '● 配信: ON' : '配信: OFF';
        const masterText = m.running ? '● コンソール: ON' : 'コンソール: OFF';
        if (svcStatus) {
          svcStatus.textContent = `${streamText} / ${masterText}`;
        }
        if (streamStatus) {
          streamStatus.textContent = `配信状態: ${s.running ? '● LIVE' : '停止中'}（port:${s.port} cam:${s.camera_id}）`;
        }
      } catch (e) {
        if (svcStatus) {
          svcStatus.textContent = '状態: 取得失敗（接続確認）';
        }
        if (streamStatus) {
          streamStatus.textContent = '配信状態: 取得失敗（接続確認）';
        }
      }
    };

    if (streamStart) {
      streamStart.addEventListener('click', async () => {
        streamStart.disabled = true;
        try {
          await apiPost('/api/stream/start');
        } finally {
          streamStart.disabled = false;
          refreshSvc();
        }
      });
    }

    if (streamStop) {
      streamStop.addEventListener('click', async () => {
        streamStop.disabled = true;
        try {
          await apiPost('/api/stream/stop');
        } finally {
          streamStop.disabled = false;
          refreshSvc();
        }
      });
    }

    if (masterStart) {
      masterStart.addEventListener('click', async () => {
        masterStart.disabled = true;
        try {
          await apiPost('/api/master/start');
        } finally {
          masterStart.disabled = false;
          refreshSvc();
        }
      });
    }

    if (masterStop) {
      masterStop.addEventListener('click', async () => {
        masterStop.disabled = true;
        try {
          await apiPost('/api/master/stop');
        } finally {
          masterStop.disabled = false;
          refreshSvc();
        }
      });
    }

    if (openStream) {
      openStream.addEventListener('click', async () => {
        if (!cached) await refreshSvc();
        const host = getBaseHost();
        const port = cached?.u?.stream_port ?? 5001;
        window.open(`http://${host}:${port}/stream`, '_blank');
      });
    }

    if (openMaster) {
      openMaster.addEventListener('click', async () => {
        if (!cached) await refreshSvc();
        const host = getBaseHost();
        const port = cached?.u?.master_port ?? 5050;
        window.open(`http://${host}:${port}/`, '_blank');
      });
    }

    if (openPredict) {
      openPredict.addEventListener('click', async () => {
        if (!cached) await refreshSvc();
        const host = getBaseHost();
        const port = cached?.u?.predict_port ?? 5100;
        window.open(`http://${host}:${port}/`, '_blank');
      });
    }

    refreshSvc();
    setInterval(refreshSvc, 3000);
  };

  init();
});
