// 状态栏记时器 — 管理页面前端
// 直接 fetch dashboard.py 起的本机 HTTP server (pywebview 6.2.1 的 js_api 在 winforms 下不可用).
// 柱状图是纯 DOM 画的, 不依赖任何图表库.

const POLL_MS = 30000;
let currentKind = 'day';
let chartMax = 60;          // 当前图的纵轴上限 (分钟)
let lastData = null;
/* ---------------- 工具 ---------------- */

async function apiGet(path) {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(`${path} -> HTTP ${resp.status}`);
  return resp.json();
}

/** 中文时长, 跟手机那个页面一致: 1 小时 58 分钟 / 5 分钟 / 不到 1 分钟 */
function fmtCn(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  if (s < 60) return s === 0 ? '0 分钟' : '不到 1 分钟';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return m > 0 ? `${h} 小时 ${m} 分钟` : `${h} 小时`;
  return `${m} 分钟`;
}

function fmtClock(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const m = Math.floor(s / 60);
  if (m >= 60) {
    const h = Math.floor(m / 60), mm = m % 60;
    return mm ? `${h} 小时 ${mm} 分钟` : `${h} 小时`;
  }
  return `${m} 分钟`;
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function hueOf(str) {
  let h = 0;
  for (let i = 0; i < String(str).length; i++) h = (h * 31 + String(str).charCodeAt(i)) % 360;
  return h;
}

/* ---------------- 数字与对比 ---------------- */

function renderHead(data) {
  document.getElementById('p-label').textContent = data.label || '';
  document.getElementById('p-total').textContent = fmtCn(data.total_seconds);

  const el = document.getElementById('p-cmp');
  const prev = Number(data.prev_seconds) || 0;
  const now = Number(data.total_seconds) || 0;
  const prevLabel = data.prev_label || '上次';
  const diff = now - prev;

  if (prev <= 0 && now <= 0) { el.textContent = ''; return; }
  if (Math.abs(diff) < 60) { el.textContent = `和${prevLabel}差不多`; return; }

  const word = diff > 0 ? '增加' : '减少';
  const cls = diff > 0 ? 'up' : 'down';
  el.innerHTML = `比${escapeHtml(prevLabel)}<span class="${cls}">${word} ${escapeHtml(fmtCn(Math.abs(diff)))}</span>`;
}

/* ---------------- 柱状图 ---------------- */

function renderChart(data) {
  const bars = data.bars || [];
  const peakMin = Math.max(0, ...bars.map(b => (Number(b.seconds) || 0) / 60));
  // 纵轴至少 0-60 分钟; 峰值超过就往上取到 30 的整数倍
  chartMax = Math.max(60, Math.ceil(peakMin / 30) * 30);

  const labels = data.axis_labels || {};
  const grid = document.querySelectorAll('.gridline > span');
  if (grid.length >= 3) {
    grid[0].textContent = `${chartMax} 分钟`;
    grid[1].textContent = `${Math.round(chartMax / 2)} 分钟`;
    grid[2].textContent = '0 分钟';
  }

  const box = document.getElementById('p-bars');
  // 柱子少 (每周) 时放宽单根宽度, 否则 4 根 30px 的条会显得很稀疏
  box.style.setProperty('--bar-w', bars.length <= 8 ? '54px' : '30px');
  box.innerHTML = bars.map((b, i) => {
    const mins = (Number(b.seconds) || 0) / 60;
    const h = mins <= 0 ? 0 : Math.max(1.5, (mins / chartMax) * 100);
    const cls = mins <= 0 ? 'col is-empty' : 'col';
    return `<div class="${cls}" data-i="${i}">
      <div class="col__inner">
        <div class="col__track"></div>
        <div class="col__fill" style="height:${h.toFixed(2)}%"></div>
      </div>
    </div>`;
  }).join('');

  // 轴标一列一个, 只给需要显示的列填文字, 这样跟柱子中心严格对齐
  const axis = document.getElementById('p-axis');
  axis.innerHTML = bars.map((_, i) =>
    `<span>${escapeHtml(labels[String(i)] || '')}</span>`).join('');

  box.querySelectorAll('.col').forEach(col => {
    col.addEventListener('mouseenter', onBarEnter);
    col.addEventListener('mouseleave', hideTip);
  });
}

function onBarEnter(ev) {
  if (!lastData) return;
  const i = Number(ev.currentTarget.dataset.i);
  const b = (lastData.bars || [])[i];
  if (!b) return;
  const label = (lastData.axis_labels || {})[String(i)] || '';
  const prefix = lastData.kind === 'day'
    ? `${i}:00 – ${i + 1}:00`
    : label;
  showTip(ev.currentTarget, `<b>${escapeHtml(prefix)}</b><br>${escapeHtml(fmtClock(b.seconds))}`);
}

function showTip(anchor, html) {
  const tip = document.getElementById('tip');
  tip.innerHTML = html;
  tip.hidden = false;
  const r = anchor.getBoundingClientRect();
  const t = tip.getBoundingClientRect();
  let left = r.left + r.width / 2 - t.width / 2;
  left = Math.max(8, Math.min(left, window.innerWidth - t.width - 8));
  let top = r.top - t.height - 8;
  if (top < 8) top = r.bottom + 8;
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}

function hideTip() {
  document.getElementById('tip').hidden = true;
}

/* ---------------- 应用列表 ---------------- */

/** exe 路径 -> base64url.
    查询串里直接放中文路径会在服务端解码时变形, 所以统一编码成 ASCII 再传. */
function b64url(str) {
  const bytes = new TextEncoder().encode(String(str));
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function renderApps(apps) {
  const box = document.getElementById('p-apps');
  const empty = document.getElementById('p-empty');
  const rows = apps || [];

  if (!rows.length) {
    box.innerHTML = '';
    empty.hidden = false;
    return;
  }
  empty.hidden = true;

  box.innerHTML = rows.map(a => {
    const name = a.name || '(unknown)';
    const pct = Math.round((Number(a.share) || 0) * 100);
    const icon = a.exe
      ? `<img src="/api/icon?b64=${b64url(a.exe)}" alt="" onload="this.classList.add('is-ok')">`
      : '';
    return `<div class="row" title="${escapeHtml(a.exe || name)}">
      <div class="ic" style="--hue:${hueOf(name)}"><span>${escapeHtml(name.slice(0, 1))}</span>${icon}</div>
      <div class="meta">
        <div class="nm">${escapeHtml(name)}</div>
        <div class="sub">${escapeHtml(fmtClock(a.seconds))} · ${pct}%</div>
      </div>
      <div class="chev">›</div>
    </div>`;
  }).join('');
}

/* ---------------- 加载 ---------------- */

let lastPayload = '';

async function load(kind) {
  const status = document.getElementById('status');
  status.textContent = '刷新中…';
  try {
    const data = await apiPage(kind);
    lastData = data;
    const payload = JSON.stringify(data);
    // 数据没变就不重画: 重画会打断选中、也会让页面滚一下
    if (payload !== lastPayload) {
      lastPayload = payload;
      const keepY = window.scrollY;
      renderHead(data);
      renderChart(data);
      renderApps(data.apps);
      window.scrollTo(0, keepY);
    }
    status.textContent = `更新于 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`;
  } catch (e) {
    status.textContent = '读取失败';
    console.error('load error', e);
  }
}

function apiPage(kind) {
  return apiGet(`/api/page/${kind}`);
}

function switchTo(kind) {
  if (kind !== 'day' && kind !== 'week') return;
  if (kind === currentKind) return;
  currentKind = kind;
  lastPayload = '';
  document.querySelectorAll('.tab').forEach(t =>
    t.classList.toggle('is-active', t.dataset.kind === kind));
  hideTip();
  try { history.replaceState(null, '', `#${kind}`); } catch (e) { /* file:// 下可能不支持 */ }
  load(kind);
}

document.getElementById('tabs').addEventListener('click', ev => {
  const btn = ev.target.closest('.tab');
  if (btn) switchTo(btn.dataset.kind);
});

window.addEventListener('hashchange', () => {
  const k = (location.hash || '').replace('#', '');
  if (k === 'day' || k === 'week') switchTo(k);
});

window.addEventListener('resize', hideTip);

(async function init() {
  // 支持 #week / #day 直接打开对应视图 (也方便调试和截图)
  const k = (location.hash || '').replace('#', '');
  if (k === 'week' || k === 'day') currentKind = k;
  document.querySelectorAll('.tab').forEach(t =>
    t.classList.toggle('is-active', t.dataset.kind === currentKind));
  await load(currentKind);
  setInterval(() => {
    if (document.hidden) return;
    const h = (location.hash || '').replace('#', '');
    if ((h === 'day' || h === 'week') && h !== currentKind) switchTo(h);
    else load(currentKind);
  }, POLL_MS);
})();
