/* ============================================================
   T1-Remote 上位机 —— 前端逻辑（原生 JS，无依赖）
   ============================================================ */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const EL = {
  stSegs: $('#stSegs'), stDur: $('#stDur'), stFps: $('#stFps'), stKb: $('#stKb'),
  tagState: $('#tagState'), tagRate: $('#tagRate'),
  liveDur: $('#liveDur'), liveFrames: $('#liveFrames'), levelFill: $('#levelFill'),
  lastKey: $('#lastKey'),
  infoList: $('#infoList'), capsList: $('#capsList'),
  trackList: $('#trackList'),
  keyGrid: $('#keyGrid'), liveKey: $('#liveKey'),
  logBody: $('#logBody'),
  devChip: $('#devChip'), devState: $('#devState'), devMac: $('#devMac'),
  bfill: $('#bfill'), batteryTxt: $('#batteryTxt'),
  btnConnect: $('#btnConnect'),
  hidDot: $('#hidDot'), hidTxt: $('#hidTxt'),
  edEmpty: $('#edEmpty'), edBody: $('#edBody'), edId: $('#edId'), edName: $('#edName'),
  edChan: $('#edChan'), edChips: $('#edChips'), edLabel: $('#edLabel'),
  edAction: $('#edAction'), edParse: $('#edParse'), edPresets: $('#edPresets'),
  edCount: $('#edCount'), extraChips: $('#extraChips'), emptyChips: $('#emptyChips'),
  toasts: $('#toasts'),
};

const S = {
  cfg: {}, keymap: {}, info: {}, caps: {}, state: { value: 'idle', text: '未连接' },
  segments: [], rate: {}, keycounts: {}, lastKey: null, speech: {},
  presets: [], vkName: {}, usageName: {}, cfgPath: '',
  keyFilter: 'all', logFilter: 'all',
  playingId: null, sel: null,
  levels: new Array(140).fill(0),
  level: 0, levelSmooth: 0,
  ripples: [],
  logs: [],
  connected: false,
};

const INK = '#17191d';
const INK_SOFT = 'rgba(23,25,29,';

/* ------------------------------------------------------------------ 工具 */
function toast(msg, kind = 'info', ms = 2400) {
  const d = document.createElement('div');
  d.className = 'toast ' + kind;
  d.innerHTML = '<span class="dot"></span><span></span>';
  d.lastChild.textContent = msg;
  EL.toasts.appendChild(d);
  setTimeout(() => { d.classList.add('out'); setTimeout(() => d.remove(), 260); }, ms);
}

async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

const fmtDur = (v) => (v || 0).toFixed(2);
const pad2 = (n) => String(n).padStart(2, '0');
function fmtTime(t0) {
  if (!t0) return '--:--:--';
  const d = new Date(t0 * 1000);
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

/* ------------------------------------------------------------------ 导航 */
function goto(page) {
  $$('.nav-btn').forEach((b) => b.classList.toggle('active', b.dataset.page === page));
  $$('.page').forEach((p) => p.classList.toggle('active', p.id === 'page-' + page));
  if (history.replaceState) history.replaceState(null, '', '#' + page);
}
$$('.nav-btn').forEach((b) => b.addEventListener('click', () => goto(b.dataset.page)));

/* ------------------------------------------------------------------ 状态 */
const STATE_TXT = { idle: '未连接', connecting: '连接中', connected: '已连接', error: '连接失败' };

function setState(st) {
  S.state = st;
  const v = st.value || 'idle';
  S.connected = v === 'connected';
  EL.devState.textContent = st.text || STATE_TXT[v] || v;
  EL.devChip.classList.toggle('on', v === 'connected');
  EL.devChip.classList.toggle('err', v === 'error');
  EL.btnConnect.textContent = (v === 'connected' || v === 'connecting') ? '断开' : '连接';
  EL.btnConnect.classList.toggle('danger', v === 'connected');
  EL.tagState.textContent = v === 'connected' ? '在线' : STATE_TXT[v];
  EL.tagState.classList.toggle('live', v === 'connected');
}

function setBattery(text) {
  const m = /(\d+)%/.exec(text || '');
  if (!m) {
    EL.batteryTxt.textContent = '—';
    EL.bfill.setAttribute('width', '0');
    return;
  }
  const pct = Math.max(0, Math.min(100, +m[1]));
  EL.batteryTxt.textContent = pct + '%';
  EL.bfill.setAttribute('width', Math.max(1.6, (10.4 * pct) / 100).toFixed(2));
  EL.bfill.style.fill = pct > 20 ? '' : '#cd4f4f';
}

function renderInfo() {
  const rows = ['厂商', '型号', '固件'];
  EL.infoList.innerHTML = rows.map((k) =>
    `<div class="info-row"><label>${k}</label><b>${S.info[k] || '—'}</b></div>`).join('');
  if (S.info['电量']) setBattery(S.info['电量']);
}

const MODEL = { 0x00: 'ON_REQUEST', 0x01: 'PTT', 0x03: 'HTT', 0x02: 'HTT', 0x04: 'HTT' };
const CODECS = { 0x02: 'IMA ADPCM · 16 kHz', 0x01: 'IMA ADPCM · 8 kHz' };

function renderCaps() {
  const c = S.caps || {};
  const modelKey = c.model ? parseInt(c.model, 16) : -1;
  const codecKey = c.codecs ? parseInt(c.codecs, 16) : -1;
  const rows = [
    ['协议版本', c.version || '—'],
    ['编码', CODECS[codecKey] || c.codecs || '—'],
    ['交互模型', MODEL[modelKey] || c.model || '—'],
    ['帧长', c.frame ? c.frame + ' B / ' + (c.frame * 2 / (c.sr || 16000) * 1000).toFixed(1) + ' ms' : '—'],
  ];
  EL.capsList.innerHTML = rows.map(([k, v]) =>
    `<div class="info-row"><label>${k}</label><b>${v}</b></div>`).join('');
  if (c.sr) EL.tagRate.textContent = (c.sr / 1000) + ' kHz · ADPCM';
}

function renderStats() {
  const segs = S.segments;
  EL.stSegs.textContent = segs.length;
  EL.stDur.textContent = (segs.reduce((a, s) => a + (s.dur || 0), 0)).toFixed(1);
  EL.stFps.textContent = S.rate.fps || 0;
  EL.stKb.textContent = Math.round(S.rate.kb || 0);
}

/* ------------------------------------------------------------------ 音轨 */
function sparkPoints(peaks) {
  if (!peaks || !peaks.length) peaks = new Array(40).fill(0.02);
  const n = peaks.length;
  const mx = Math.max(0.02, ...peaks.map((v) => Math.abs(v)));
  const norm = peaks.map((v) => Math.min(1, Math.pow(Math.abs(v) / mx, 0.7)));
  const pts = [], back = [];
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * 100;
    const h = Math.max(0.05, norm[i]);
    pts.push(`${x.toFixed(1)},${(17 - h * 15.5).toFixed(1)}`);
  }
  for (let i = n - 1; i >= 0; i--) {
    const x = (i / (n - 1)) * 100;
    const h = Math.max(0.05, norm[i]);
    back.push(`${x.toFixed(1)},${(17 + h * 15.5).toFixed(1)}`);
  }
  return pts.concat(back).join(' ');
}

function trackRow(seg) {
  const el = document.createElement('div');
  el.className = 'track';
  el.dataset.id = seg.id;
  const badgeOk = seg.reason && !/超时|错误|error/i.test(seg.reason);
  el.innerHTML = `
    <button class="play" title="播放">
      <svg viewBox="0 0 24 24"><path d="M8 5.5v13l11-6.5z"/></svg>
    </button>
    <div class="meta">
      <b>${fmtDur(seg.dur)} s</b>
      <span>${fmtTime(seg.t0)} · #${seg.id}</span>
    </div>
    <div class="spark-wrap" style="position:relative">
      <svg class="spark" viewBox="0 0 100 34" preserveAspectRatio="none">
        <polyline points="${sparkPoints(seg.peaks)}" vector-effect="non-scaling-stroke"/>
      </svg>
      <div class="spark-prog" style="position:absolute;left:0;top:0;bottom:0;width:0;
           background:rgba(23,25,29,.055);border-radius:3px;pointer-events:none"></div>
    </div>
    <div class="badges">
      <span class="badge">${seg.frames} 帧</span>
      <span class="badge ${badgeOk ? 'ok' : ''}">${seg.reason || '录音中'}</span>
    </div>
    <div class="acts">
      <button class="mini" data-act="save" title="保存到输出目录">
        <svg viewBox="0 0 24 24"><path d="M12 3v12m0 0 4-4m-4 4-4-4M4 17v3h16v-3"/></svg>
      </button>
      <button class="mini" data-act="raw" title="下载 ADPCM">
        <svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h10M4 18h13"/></svg>
      </button>
      <button class="mini danger" data-act="del" title="删除">
        <svg viewBox="0 0 24 24"><path d="M5 7h14M9 7V5h6v2M7 7l1 13h8l1-13"/></svg>
      </button>
    </div>`;
  el.querySelector('.play').addEventListener('click', () => playSeg(seg.id, el));
  el.querySelectorAll('.mini').forEach((b) => b.addEventListener('click', async (e) => {
    e.stopPropagation();
    const act = b.dataset.act;
    if (act === 'save') {
      const r = await api('/api/seg/save', { id: seg.id });
      toast(r.ok ? '已保存 ' + r.path.split(/[\\/]/).pop() : '保存失败', r.ok ? 'ok' : 'err');
    } else if (act === 'raw') {
      location.href = `/api/seg/${seg.id}.adpcm`;
    } else if (act === 'del') {
      await api('/api/seg/delete', { id: seg.id });
      S.segments = S.segments.filter((x) => x.id !== seg.id);
      renderTracks(); renderStats();
      toast('已删除 #' + seg.id, 'warn');
    }
  }));
  return el;
}

function renderTracks() {
  if (!S.segments.length) {
    EL.trackList.innerHTML = '<div class="empty">还没有音轨 — 按住遥控器语音键说一句话试试</div>';
    return;
  }
  const frag = document.createDocumentFragment();
  S.segments.slice().reverse().forEach((s) => frag.appendChild(trackRow(s)));
  EL.trackList.innerHTML = '';
  EL.trackList.appendChild(frag);
}

let audioEl = null;
function playSeg(id, row) {
  if (!audioEl) {
    audioEl = new Audio();
    audioEl.addEventListener('timeupdate', () => {
      const r = $('.track[data-id="' + S.playingId + '"]');
      if (!r || !audioEl.duration) return;
      r.querySelector('.spark-prog').style.width =
        ((audioEl.currentTime / audioEl.duration) * 100).toFixed(1) + '%';
    });
    audioEl.addEventListener('ended', () => {
      const r = $('.track[data-id="' + S.playingId + '"]');
      if (r) r.querySelector('.play svg path').setAttribute('d', 'M8 5.5v13l11-6.5z');
      S.playingId = null;
    });
  }
  if (S.playingId === id && !audioEl.paused) {
    audioEl.pause();
    row.querySelector('.play svg path').setAttribute('d', 'M8 5.5v13l11-6.5z');
    S.playingId = null;
    return;
  }
  $$('.track .play svg path').forEach((p) => p.setAttribute('d', 'M8 5.5v13l11-6.5z'));
  S.playingId = id;
  audioEl.src = `/api/seg/${id}.wav?t=${Date.now()}`;
  audioEl.play().then(() => {
    row.querySelector('.play svg path').setAttribute('d', 'M8 7h3v10H8zM13 7h3v10h-3z');
  }).catch(() => toast('无法播放该音轨', 'err'));
}

/* ------------------------------------------------------------------ 遥控器热区 */
const REMOTE = $('#remoteFlip') || $('.remote');
let flipDragMoved = false;

function hotEls() { return $$('.hot', REMOTE); }
function hotKeys(el) {
  return (el.dataset.keys || '').split(',').map((s) => s.trim()).filter(Boolean);
}
function hotOf(id) { return hotEls().find((el) => hotKeys(el).includes(id)); }

function paintHot() {
  const km = S.cfg.keymap || {};
  hotEls().forEach((el) => {
    const keys = hotKeys(el);
    el.classList.toggle('off', keys.length === 0);
    el.classList.toggle('sel', !!S.sel && keys.includes(S.sel));
    const bound = keys.some((k) => ((km[k] || {}).action || '').trim());
    el.classList.toggle('bound', bound);
  });
}

function bindHots() {
  $$('.hot', REMOTE).forEach((el) => {
    if (el.dataset.bound === '1') return;
    el.dataset.bound = '1';
    el.addEventListener('click', () => {
      // 拖拽翻转时松手不应当成点击
      if (flipDragMoved) { flipDragMoved = false; return; }
      const keys = hotKeys(el);
      if (!keys.length) {
        toast('这个键没有上报 HID 事件，无法映射', 'warn', 3000);
        return;
      }
      selectKey(keys[0]);
    });
  });
}

/* 背面键盘：标准 HID 键盘的五行，旋转 90° 后竖排显示 */
const KB_ROWS = [
  [['Esc', 0x1B], ['1', 0x31], ['2', 0x32], ['3', 0x33], ['4', 0x34], ['5', 0x35],
   ['6', 0x36], ['7', 0x37], ['8', 0x38], ['9', 0x39], ['0', 0x30],
   ['-', 0xBD], ['=', 0xBB], ['Back', 0x08]],
  [['Tab', 0x09], ['Q', 0x51], ['W', 0x57], ['E', 0x45], ['R', 0x52], ['T', 0x54],
   ['Y', 0x59], ['U', 0x55], ['I', 0x49], ['O', 0x4F], ['P', 0x50],
   ['[', 0xDB], [']', 0xDD], ['\\', 0xDC]],
  [['Caps', 0x14], ['A', 0x41], ['S', 0x53], ['D', 0x44], ['F', 0x46], ['G', 0x47],
   ['H', 0x48], ['J', 0x4A], ['K', 0x4B], ['L', 0x4C], [';', 0xBA], ["'", 0xDE],
   ['Enter', 0x0D]],
  [['Shift', 0x10], ['Z', 0x5A], ['X', 0x58], ['C', 0x43], ['V', 0x56], ['B', 0x42],
   ['N', 0x4E], ['M', 0x4D], [',', 0xBC], ['.', 0xBE], ['/', 0xBF], ['Shift', 0x10]],
  [['Ctrl', 0x11], ['Win', 0x5B], ['Alt', 0x12], ['Space', 0x20], ['Fn', null],
   ['←', 0x25], ['↑', 0x26], ['↓', 0x28], ['→', 0x27]],
];

function buildBackFace() {
  const svg = document.getElementById('remoteBack');
  if (!svg || svg.dataset.built === '1') return;
  svg.dataset.built = '1';
  const NS = 'http://www.w3.org/2000/svg';
  const mk = (tag, attrs) => {
    const el = document.createElementNS(NS, tag);
    Object.keys(attrs).forEach((k) => el.setAttribute(k, attrs[k]));
    return el;
  };

  // 机身
  svg.appendChild(mk('rect', {
    x: 2, y: 2, width: 196, height: 696, rx: 26,
    fill: '#efefec', stroke: '#d6d6d1', 'stroke-width': 1.5,
  }));
  svg.appendChild(mk('rect', {
    x: 13, y: 22, width: 174, height: 656, rx: 12,
    fill: 'none', stroke: '#dedeD9', 'stroke-width': 1,
  }));

  const W = 32, H = 40, GAP = 4;
  const cols = KB_ROWS.length;
  const x0 = (200 - (cols * W + (cols - 1) * GAP)) / 2;
  const y0 = 30;

  KB_ROWS.forEach((row, ci) => {
    const x = x0 + ci * (W + GAP);
    row.forEach(([label, vk], ri) => {
      const y = y0 + ri * (H + GAP);
      const keyId = vk === null ? '' : 'K:0x' + vk.toString(16).toUpperCase().padStart(2, '0');
      const g = mk('g', {
        class: 'hot kb' + (keyId ? '' : ' off'),
        'data-keys': keyId,
        'data-name': label,
      });
      g.appendChild(mk('rect', {
        class: 'face', x, y, width: W, height: H, rx: 7,
      }));
      const t = mk('text', {
        class: 'kbt', x: x + W / 2, y: y + H / 2,
        'font-size': label.length > 2 ? 7.5 : (label.length > 1 ? 9.5 : 12),
      });
      t.textContent = label;
      g.appendChild(t);
      const title = mk('title', {});
      title.textContent = keyId ? label + '  ' + keyId : label + '（无上报码）';
      g.appendChild(title);
      svg.appendChild(g);
    });
  });
}

/* 滑动 / 拖拽翻转 */
function initFlip() {
  const stage = $('.remote-stage');
  const flip = $('#remoteFlip');
  if (!stage || !flip) return;
  let downX = 0, moved = false, dragging = false;

  stage.addEventListener('pointerdown', (e) => {
    downX = e.clientX;
    moved = false;
    flipDragMoved = false;
    dragging = true;
    flip.classList.add('dragging');
  });

  stage.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    const dx = e.clientX - downX;
    if (!moved && Math.abs(dx) > 6) moved = true;
    if (!moved) return;
    const base = flip.classList.contains('flipped') ? 180 : 0;
    const ang = base + Math.max(-180, Math.min(180, dx * 0.55));
    flip.style.transform = 'rotateY(' + ang + 'deg)';
  });

  const finish = (e) => {
    if (!dragging) return;
    dragging = false;
    flip.classList.remove('dragging');
    flip.style.transform = '';
    const dx = (e.clientX || 0) - downX;
    if (moved && Math.abs(dx) > 45) flip.classList.toggle('flipped');
    flipDragMoved = moved;
  };
  stage.addEventListener('pointerup', finish);
  stage.addEventListener('pointercancel', finish);
  stage.addEventListener('pointerleave', finish);

  // 支持直接打开背面：地址栏加 #back
  if (location.hash === '#back') flip.classList.add('flipped');

  const hint = $('#flipHint');
  if (hint) {
    const sync = () => {
      hint.textContent = flip.classList.contains('flipped')
        ? '滑动翻转 · 回到正面' : '滑动翻转 · 背面键盘';
    };
    stage.addEventListener('pointerup', sync);
    sync();
  }
}

buildBackFace();
bindHots();
initFlip();

function renderExtra() {
  const covered = new Set();
  hotEls().forEach((el) => hotKeys(el).forEach((k) => covered.add(k)));
  const rest = Object.keys(S.cfg.keymap || {}).filter((k) => !covered.has(k));
  if (!rest.length) {
    EL.extraChips.innerHTML = '<span style="font-size:10.5px;color:#b3b7bc">无</span>';
    return;
  }
  EL.extraChips.innerHTML = rest.map((k) =>
    `<button data-k="${k}">${k} ${(S.cfg.keymap[k] || {}).label || ''}</button>`).join('');
  $$('button', EL.extraChips).forEach((b) =>
    b.addEventListener('click', () => selectKey(b.dataset.k)));
}

/* ------------------------------------------------------------------ 按键编辑 */
function guessLabel(id) {
  const el = hotOf(id);
  const nm = (el && el.dataset.name) || S.vkName[id] || S.usageName[id] || '';
  return nm && nm !== id ? nm : id;
}

function selectKey(id) {
  if (!id) return;
  const km = S.cfg.keymap || (S.cfg.keymap = {});
  if (!km[id]) km[id] = { label: guessLabel(id), action: '' };
  S.sel = id;
  if (!$('#page-cfg').classList.contains('active')) goto('cfg');
  renderEditor();
  paintHot();
  renderExtra();
}

let parseTimer = null;
function updateParse() {
  const act = EL.edAction.value.trim();
  clearTimeout(parseTimer);
  parseTimer = setTimeout(async () => {
    try {
      const r = await api('/api/action/describe', { action: act });
      EL.edParse.textContent = '解析：' + r.desc;
    } catch (e) { /* 忽略 */ }
  }, 160);
}

function renderEmptyChips() {
  const km = S.cfg.keymap || {};
  const ids = Object.keys(km).sort((a, b) => {
    const sa = a.startsWith('K:') ? 0 : 1, sb = b.startsWith('K:') ? 0 : 1;
    if (sa !== sb) return sa - sb;
    return parseInt(a.split(':')[1], 16) - parseInt(b.split(':')[1], 16);
  });
  EL.emptyChips.innerHTML = ids.map((k) =>
    `<button data-k="${k}">${k}${(km[k] || {}).label ? ' · ' + km[k].label : ''}</button>`).join('');
  $$('button', EL.emptyChips).forEach((b) =>
    b.addEventListener('click', () => selectKey(b.dataset.k)));
}

function renderEditor() {
  const km = S.cfg.keymap || {};
  const id = S.sel;
  if (!id || !km[id]) {
    EL.edEmpty.hidden = false;
    EL.edBody.hidden = true;
    renderEmptyChips();
    return;
  }
  EL.edEmpty.hidden = true;
  EL.edBody.hidden = false;

  const it = km[id];
  EL.edId.textContent = id;
  EL.edName.textContent = it.label || '未命名';
  EL.edChan.textContent = id.startsWith('K:') ? '键盘集合 COL01' : 'Consumer COL02';

  const host = hotOf(id);
  const group = host ? hotKeys(host) : [id];
  if (group.length > 1) {
    EL.edChips.hidden = false;
    EL.edChips.innerHTML = group.map((k) => {
      const lb = (km[k] || {}).label || '';
      return `<button data-k="${k}" class="${k === id ? 'active' : ''}">${k}${lb ? ' · ' + lb : ''}</button>`;
    }).join('');
    $$('button', EL.edChips).forEach((b) =>
      b.addEventListener('click', () => selectKey(b.dataset.k)));
  } else {
    EL.edChips.hidden = true;
    EL.edChips.innerHTML = '';
  }

  EL.edLabel.value = it.label || '';
  EL.edAction.value = it.action || '';
  EL.edCount.textContent = S.keycounts[id] || 0;

  EL.edPresets.innerHTML = S.presets.map(([label], i) =>
    `<button data-i="${i}">${label}</button>`).join('');
  $$('button', EL.edPresets).forEach((b) => b.addEventListener('click', () => {
    EL.edAction.value = S.presets[+b.dataset.i][1];
    updateParse();
  }));

  updateParse();
}

EL.edAction.addEventListener('input', updateParse);

$('#edSave').addEventListener('click', async () => {
  const id = S.sel;
  if (!id) return;
  const r = await api('/api/key', {
    id, label: EL.edLabel.value.trim() || '未命名', action: EL.edAction.value.trim(),
  });
  S.cfg.keymap = r.keymap;
  renderEditor(); renderKeys(); paintHot(); renderExtra();
  toast('已保存 ' + id, 'ok');
});

$('#edDel').addEventListener('click', async () => {
  const id = S.sel;
  if (!id) return;
  const host = hotOf(id);
  const group = host ? hotKeys(host).filter((k) => k !== id) : [];
  const r = await api('/api/key', { id, delete: true });
  S.cfg.keymap = r.keymap;
  S.sel = group.length ? group[0] : null;
  renderEditor(); renderKeys(); paintHot(); renderExtra();
  toast('已移除 ' + id, 'warn');
});

$('#edTest').addEventListener('click', async () => {
  const act = EL.edAction.value.trim();
  if (!act) return toast('动作为空', 'warn');
  await api('/api/action/test', { action: act });
  toast('已试运行：' + act, 'ok');
});

$('#btnKeyReset').addEventListener('click', async () => {
  const r = await api('/api/key/reset', {});
  S.cfg.keymap = r.keymap;
  if (S.sel && !S.cfg.keymap[S.sel]) S.sel = null;
  renderEditor(); renderKeys(); paintHot(); renderExtra();
  toast('按键映射已恢复默认', 'ok');
});

/* ------------------------------------------------------------------ 按键列表 */
function keyList() {
  const km = S.cfg.keymap || {};
  let list = Object.keys(km).map((k) => ({ id: k, ...km[k] }));
  if (S.keyFilter === 'K' || S.keyFilter === 'C') {
    list = list.filter((x) => x.id.startsWith(S.keyFilter + ':'));
  } else if (S.keyFilter === 'bound') {
    list = list.filter((x) => (x.action || '').trim());
  }
  list.sort((a, b) => {
    const sa = a.id.startsWith('K:') ? 0 : 1;
    const sb = b.id.startsWith('K:') ? 0 : 1;
    if (sa !== sb) return sa - sb;
    return parseInt(a.id.split(':')[1], 16) - parseInt(b.id.split(':')[1], 16);
  });
  return list;
}

function renderKeys() {
  const list = keyList();
  if (!list.length) {
    EL.keyGrid.innerHTML = '<div class="empty">这个筛选下没有按键 — 按一下遥控器，新的键会自动出现</div>';
    return;
  }
  EL.keyGrid.innerHTML = list.map((k) => {
    const chan = k.id.startsWith('K:') ? 'k' : 'c';
    const cnt = S.keycounts[k.id] || 0;
    const act = (k.action || '').trim();
    return `<div class="kcard ${k.id === S.sel ? 'sel' : ''}" data-id="${k.id}">
      <div class="k-top">
        <span class="chan ${chan}">${chan === 'k' ? 'KEY' : 'CSMR'}</span>
        <span class="k-name">${k.label || '未命名'}</span>
        ${cnt ? `<span class="k-count">${cnt}×</span>` : ''}
      </div>
      <div class="k-id">${k.id}</div>
      <div class="k-act ${act ? '' : 'empty-act'}">${act || '仅记录 · 未绑定动作'}</div>
    </div>`;
  }).join('');
  $$('.kcard', EL.keyGrid).forEach((c) =>
    c.addEventListener('click', () => selectKey(c.dataset.id)));
}

$$('#keyFilter button').forEach((b) => b.addEventListener('click', () => {
  $$('#keyFilter button').forEach((x) => x.classList.toggle('active', x === b));
  S.keyFilter = b.dataset.f;
  renderKeys();
}));

/* ------------------------------------------------------------------ 设置 */
function bindCfg() {
  $$('[data-cfg]').forEach((inp) => {
    const k = inp.dataset.cfg;
    if (S.cfg[k] !== undefined) inp.value = S.cfg[k];
    inp.addEventListener('change', () => pushCfg({ [k]: inp.value.trim() }));
  });

  $$('[data-bool]').forEach((sw) => {
    const k = sw.dataset.bool;
    sw.classList.toggle('on', !!S.cfg[k]);
    sw.addEventListener('click', () => {
      const v = !sw.classList.contains('on');
      sw.classList.toggle('on', v);
      pushCfg({ [k]: v });
    });
  });

  const UNIT = { skip_bytes: 'B', hpf_hz: 'Hz', mic_extend_ms: 'ms', max_segment_s: 's' };
  $$('[data-num]').forEach((sl) => {
    const k = sl.dataset.num;
    const out = $('#v_' + k);
    const show = (v) => v + (UNIT[k] ? ' ' + UNIT[k] : '');
    if (S.cfg[k] !== undefined) { sl.value = S.cfg[k]; out.textContent = show(S.cfg[k]); }
    const paint = () => {
      const pct = (sl.value - sl.min) / (sl.max - sl.min) * 100;
      sl.style.background = `linear-gradient(90deg, ${INK} 0%, ${INK} ${pct}%, #e6e6e1 ${pct}%)`;
    };
    paint();
    sl.addEventListener('input', () => { out.textContent = show(sl.value); paint(); });
    sl.addEventListener('change', () => pushCfg({ [k]: +sl.value }));
  });
}

let cfgTimer = null, cfgPending = {};
function pushCfg(patch) {
  Object.assign(cfgPending, patch);
  clearTimeout(cfgTimer);
  cfgTimer = setTimeout(async () => {
    const body = cfgPending; cfgPending = {};
    Object.assign(S.cfg, body);
    await api('/api/config', { cfg: body });
    if ('mac' in body) EL.devMac.textContent = body.mac || '——';
  }, 220);
}

$('#btnPickDir').addEventListener('click', async () => {
  toast('正在打开目录选择窗口…', 'info', 1400);
  const r = await api('/api/pickdir', {});
  if (r.ok && r.path) {
    $('[data-cfg=outdir]').value = r.path;
    pushCfg({ outdir: r.path });
    toast('输出目录已更新', 'ok');
  }
});

$('#btnOpenCfg').addEventListener('click', async () => {
  await api('/api/open/dir', { path: '' }).catch(() => {});
  toast('配置文件：' + (S.cfgPath || ''), 'info', 3200);
});

$('#btnResetCfg').addEventListener('click', () => {
  if (!confirm('恢复全部配置为默认值？按键映射不受影响。')) return;
  location.reload();
});

/* ------------------------------------------------------------------ 日志 */
function logLine(item) {
  const d = document.createElement('div');
  d.className = 'log-line ' + (item.level || 'info');
  d.dataset.lv = item.level || 'info';
  d.innerHTML = `<span class="ts">${item.ts || ''}</span>
    <span class="lv">${(item.level || 'info').toUpperCase()}</span>
    <span class="msg"></span>`;
  d.lastChild.textContent = item.text || '';
  return d;
}

function appendLog(item) {
  S.logs.push(item);
  if (S.logs.length > 600) S.logs.shift();
  if (S.logFilter !== 'all' && item.level !== S.logFilter) return;
  const near = EL.logBody.scrollHeight - EL.logBody.scrollTop - EL.logBody.clientHeight < 80;
  EL.logBody.appendChild(logLine(item));
  while (EL.logBody.children.length > 600) EL.logBody.firstChild.remove();
  if (near) EL.logBody.scrollTop = EL.logBody.scrollHeight;
}

$$('#logFilter button').forEach((b) => b.addEventListener('click', () => {
  $$('#logFilter button').forEach((x) => x.classList.toggle('active', x === b));
  S.logFilter = b.dataset.l;
  EL.logBody.innerHTML = '';
  S.logs.filter((l) => S.logFilter === 'all' || (l.level || 'info') === S.logFilter)
    .forEach((l) => EL.logBody.appendChild(logLine(l)));
  EL.logBody.scrollTop = EL.logBody.scrollHeight;
}));

$('#btnClearLog').addEventListener('click', async () => {
  await api('/api/logs/clear', {});
  S.logs = [];
  EL.logBody.innerHTML = '';
});

/* ------------------------------------------------------------------ 顶栏动作 */
EL.btnConnect.addEventListener('click', async () => {
  if (S.connected) {
    await api('/api/disconnect', {});
    toast('正在断开…', 'warn');
  } else {
    const mac = $('[data-cfg=mac]').value.trim();
    const name = $('[data-cfg=name]').value.trim();
    await api('/api/connect', { mac, name });
    toast(mac ? ('正在连接 ' + mac) : (name ? ('按名称查找 ' + name) : '还没填地址或名称'), 'info');
  }
});

$('#btnCaps').addEventListener('click', async () => {
  try {
    await api('/api/caps', {});
    toast('已请求能力表', 'info');
  } catch (e) { toast('未连接', 'err'); }
});

$('#btnOutdir').addEventListener('click', async () => { await api('/api/open/dir', {}); });

$('#btnSaveAll').addEventListener('click', async () => {
  let n = 0;
  for (const s of S.segments) {
    try { const r = await api('/api/seg/save', { id: s.id }); if (r.ok) n++; } catch (e) {}
  }
  toast(`已保存 ${n} 个音轨到输出目录`, 'ok');
});

/* ------------------------------------------------------------------ 画布 */
const orb = $('#orb'), wave = $('#wave');
const octx = orb.getContext('2d'), wctx = wave.getContext('2d');

function fitCanvas(c) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const r = c.getBoundingClientRect();
  if (!r.width) return;
  c.width = Math.round(r.width * dpr);
  c.height = Math.round(r.height * dpr);
  c.getContext('2d').setTransform(dpr, 0, 0, dpr, 0, 0);
  return { w: r.width, h: r.height };
}

let orbSize = { w: 300, h: 300 }, waveSize = { w: 700, h: 110 };
function resizeAll() {
  orbSize = fitCanvas(orb) || orbSize;
  waveSize = fitCanvas(wave) || waveSize;
}
window.addEventListener('resize', resizeAll);

function drawOrb(t) {
  const { w, h } = orbSize;
  const cx = w / 2, cy = h / 2;
  octx.clearRect(0, 0, w, h);

  const lvl = S.levelSmooth;
  const R = Math.min(w, h) * 0.30;

  // 内盘
  const disc = octx.createRadialGradient(cx, cy - R * 0.5, 4, cx, cy, R * 1.04);
  disc.addColorStop(0, INK_SOFT + '.07)');
  disc.addColorStop(1, INK_SOFT + '.015)');
  octx.fillStyle = disc;
  octx.beginPath(); octx.arc(cx, cy, R * 1.04, 0, Math.PI * 2); octx.fill();

  // 刻度环
  const ticks = 72;
  for (let i = 0; i < ticks; i++) {
    const ang = (i / ticks) * Math.PI * 2 - Math.PI / 2;
    const active = (i / ticks) < lvl;
    const len = i % 6 === 0 ? 8 : 4;
    const r0 = R + 17, r1 = r0 + len;
    octx.beginPath();
    octx.strokeStyle = active ? INK_SOFT + '.5)' : INK_SOFT + '.13)';
    octx.lineWidth = i % 6 === 0 ? 1.4 : 1;
    octx.moveTo(cx + Math.cos(ang) * r0, cy + Math.sin(ang) * r0);
    octx.lineTo(cx + Math.cos(ang) * r1, cy + Math.sin(ang) * r1);
    octx.stroke();
  }

  // 主环
  octx.beginPath();
  octx.strokeStyle = INK_SOFT + '.22)';
  octx.lineWidth = 1;
  octx.arc(cx, cy, R, 0, Math.PI * 2);
  octx.stroke();

  // 电平弧
  if (lvl > 0.01) {
    octx.beginPath();
    octx.strokeStyle = INK;
    octx.lineWidth = 3;
    octx.lineCap = 'round';
    octx.arc(cx, cy, R, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * lvl);
    octx.stroke();
  }

  // 涟漪
  for (let i = S.ripples.length - 1; i >= 0; i--) {
    const rp = S.ripples[i];
    const p = (t - rp.t0) / 1500;
    if (p >= 1) { S.ripples.splice(i, 1); continue; }
    octx.beginPath();
    octx.strokeStyle = INK_SOFT + ((1 - p) * 0.14).toFixed(3) + ')';
    octx.lineWidth = 1;
    octx.arc(cx, cy, R * (0.7 + p * 0.9), 0, Math.PI * 2);
    octx.stroke();
  }
}

function roundRect(c, x, y, w, h, r) {
  c.moveTo(x + r, y);
  c.arcTo(x + w, y, x + w, y + h, r);
  c.arcTo(x + w, y + h, x, y + h, r);
  c.arcTo(x, y + h, x, y, r);
  c.arcTo(x, y, x + w, y, r);
  c.closePath();
}

function drawWave() {
  const { w, h } = waveSize;
  wctx.clearRect(0, 0, w, h);
  const n = S.levels.length;
  const mid = h * 0.54;
  const gap = w / n;
  const bw = Math.max(2, gap * 0.44);

  wctx.fillStyle = INK_SOFT + '.78)';
  for (let i = 0; i < n; i++) {
    const v = S.levels[i];
    if (v < 0.004) continue;
    const hh = Math.max(1.2, Math.pow(v, 0.72) * (h * 0.40));
    const x = i * gap + gap / 2 - bw / 2;
    const r = Math.min(bw / 2, 1.6);
    wctx.beginPath();
    roundRect(wctx, x, mid - hh, bw, hh * 2, r);
    wctx.fill();
  }

  wctx.strokeStyle = INK_SOFT + '.1)';
  wctx.lineWidth = 1;
  wctx.beginPath(); wctx.moveTo(0, mid); wctx.lineTo(w, mid); wctx.stroke();
}

let lastRipple = 0, prevT = 0;
function loop(t) {
  const dt = Math.min(64, t - prevT || 16);
  prevT = t;

  S.levelSmooth += (S.level - S.levelSmooth) * Math.min(1, dt / 90);
  if (S.level - S.levelSmooth > 0.14 && t - lastRipple > 420) {
    S.ripples.push({ t0: t }); lastRipple = t;
  }

  const speed = Math.max(1, Math.round(dt / 16));
  for (let k = 0; k < speed; k++) {
    S.levels.push(S.levelSmooth * (0.55 + Math.random() * 0.45));
    S.levels.shift();
  }

  if ($('#page-rec').classList.contains('active')) {
    drawOrb(t);
    drawWave();
  }
  requestAnimationFrame(loop);
}

/* ------------------------------------------------------------------ SSE */
let es = null;
function connectSSE() {
  if (es) es.close();
  es = new EventSource('/api/events');
  es.onmessage = (e) => {
    let ev;
    try { ev = JSON.parse(e.data); } catch (_) { return; }
    handle(ev);
  };
  es.onerror = () => { setTimeout(connectSSE, 1800); };
}

function flashHot(id) {
  const hosts = hotEls().filter((el) => hotKeys(el).includes(id));
  hosts.forEach((el) => {
    el.classList.add('on');
    setTimeout(() => el.classList.remove('on'), 200);
  });
}

function handle(ev) {
  switch (ev.t) {
    case 'state': setState(ev); break;
    case 'info':
      S.info[ev.key] = ev.value;
      renderInfo();
      break;
    case 'caps':
      Object.assign(S.caps, ev); delete S.caps.t;
      renderCaps();
      break;
    case 'seg_begin':
      S.segments.push({ id: ev.id, sid: ev.sid, dur: 0, frames: 0, sr: ev.sr,
        t0: Date.now() / 1000, reason: '录音中', peaks: [], live: true });
      renderTracks(); renderStats();
      break;
    case 'audio':
      S.level = ev.level || 0;
      EL.liveDur.textContent = fmtDur(ev.dur);
      EL.liveFrames.textContent = ev.frames || 0;
      EL.levelFill.style.width = Math.round(Math.min(1, ev.level || 0) * 100) + '%';
      {
        const seg = S.segments.find((s) => s.id === ev.seg);
        if (seg) { seg.dur = ev.dur || 0; seg.frames = ev.frames || 0; }
      }
      break;
    case 'seg_end': {
      const seg = S.segments.find((s) => s.id === ev.id);
      if (seg) {
        Object.assign(seg, { dur: ev.dur || 0, frames: ev.frames || 0,
          nbytes: ev.nbytes, reason: ev.reason, live: false, t0: ev.t0 });
      } else {
        S.segments.push({ id: ev.id, dur: ev.dur, frames: ev.frames,
          nbytes: ev.nbytes, reason: ev.reason, sr: ev.sr, t0: ev.t0, peaks: [] });
      }
      S.level = 0; EL.levelFill.style.width = '0%';
      renderTracks(); renderStats();
      if (S.cfg.auto_save && (ev.frames || 0) >= 4) {
        api('/api/seg/save', { id: ev.id }).then((r) => {
          if (r.ok) toast('已保存 ' + r.path.split(/[\\/]/).pop(), 'ok', 1800);
        }).catch(() => {});
      }
      break;
    }
    case 'peaks': {
      const seg = S.segments.find((s) => s.id === ev.id);
      if (seg) { seg.peaks = ev.peaks || []; renderTracks(); }
      break;
    }
    case 'rate':
      S.rate = { fps: ev.fps, frames: ev.frames, kb: ev.kb };
      renderStats();
      break;
    case 'rawkey': {
      S.keycounts[ev.id] = ev.count;
      S.lastKey = ev;
      EL.lastKey.textContent = `${ev.id} ${ev.label || ''}`;
      EL.liveKey.textContent = `${ev.id}  ${ev.label || ''}`;
      EL.liveKey.classList.add('hit');
      setTimeout(() => EL.liveKey.classList.remove('hit'), 400);
      flashHot(ev.id);

      if (S.sel === ev.id) EL.edCount.textContent = ev.count;
      const card = $(`.kcard[data-id="${ev.id}"]`);
      if (card) {
        card.classList.add('flash');
        setTimeout(() => card.classList.remove('flash'), 420);
        const c = card.querySelector('.k-count');
        if (c) c.textContent = ev.count + '×';
      }
      if (!(S.cfg.keymap || {})[ev.id]) {
        S.cfg.keymap[ev.id] = { label: ev.label || guessLabel(ev.id), action: '' };
        renderKeys(); renderExtra(); paintHot();
        if (S.sel === ev.id) renderEditor();
      }
      break;
    }
    case 'log': appendLog(ev); break;
    case 'hid':
      EL.hidDot.classList.toggle('on', !!ev.ok);
      EL.hidTxt.textContent = ev.ok ? '监听' : '未监听';
      if (!ev.ok) toast('Raw Input 启动失败：' + ev.msg, 'err', 4000);
      break;
    default: break;
  }
}

/* ------------------------------------------------------------------ 启动 */
async function boot() {
  resizeAll();
  requestAnimationFrame(loop);

  try {
    const b = await api('/api/bootstrap');
    S.cfg = b.cfg; S.cfg.keymap = S.cfg.keymap || {};
    S.info = b.info || {}; S.caps = b.caps || {};
    S.segments = b.segments || []; S.rate = b.rate || {};
    S.keycounts = b.keycounts || {}; S.lastKey = b.last_key;
    S.presets = b.presets || []; S.cfgPath = b.cfg_path;
    S.vkName = b.vk_name || {}; S.usageName = b.usage_name || {};

    setState(b.state || { value: 'idle' });
    renderInfo(); renderCaps(); renderTracks(); renderStats();
    renderKeys(); renderEditor(); paintHot(); renderExtra();
    bindCfg();

    EL.devMac.textContent = S.cfg.mac || '——';
    if (!S.cfg.mac) $('[data-cfg=mac]').value = '';
    EL.hidDot.classList.toggle('on', !!b.hid);
    EL.hidTxt.textContent = b.hid ? '监听' : '未监听';

    (b.logs || []).forEach((l) => { S.logs.push(l); });
    const view = (b.logs || []).filter((l) => S.logFilter === 'all' || l.level === S.logFilter);
    EL.logBody.innerHTML = '';
    view.forEach((l) => EL.logBody.appendChild(logLine(l)));
    EL.logBody.scrollTop = EL.logBody.scrollHeight;

    if (S.lastKey) EL.liveKey.textContent = `${S.lastKey.id}  ${S.lastKey.label || ''}`;
  } catch (e) {
    toast('无法读取后端状态：' + e.message, 'err', 5000);
  }

  connectSSE();
  setInterval(() => { if (!S.connected) renderStats(); }, 4000);

  const qp = new URLSearchParams(location.search);
  const wanted = qp.get('page') || location.hash.replace('#', '');
  if (wanted) goto(wanted);
  const openId = qp.get('open');
  if (openId) setTimeout(() => selectKey(openId), 300);
}

boot();
