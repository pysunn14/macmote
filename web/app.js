'use strict';
// The remote itself: state polling, tiles, sliders, toasts.

const BATTERY = { 'charging': 'bat.charging', 'charged': 'bat.charged', 'discharging': 'bat.discharging', 'AC attached': 'bat.attached', 'finishing charge': 'bat.finishing' };
const SOURCE = { 'AC Power': 'src.ac', 'Battery Power': 'src.battery', 'UPS Power': 'src.ups' };
const DONE = { display_off: 'done.display_off', display_on: 'done.display_on', lock: 'done.lock', away: 'done.away', mute: null, keyboard: 'done.keyboard' };
const UNREACHED = { display_off: 'unreached.display_off', display_on: 'unreached.display_on', lock: 'unreached.lock', away: 'unreached.away' };

let state = null;
const busy = new Set();
let toastTimer = 0;
const SECURE = window.isSecureContext;  // passkeys and push need the HTTPS origin

function toast(message, error = false) {
  const t = $('toast');
  t.textContent = message;
  t.classList.toggle('err', error);
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2600);
}

function setConn(ok) {
  $('conn').classList.toggle('ok', ok);
  $('conn').classList.toggle('bad', !ok);
  $('connText').textContent = ok ? t('conn.ok') : t('conn.lost');
}

async function call(method, path, body) {
  const options = { method, cache: 'no-store' };
  if (body) {
    options.headers = { 'Content-Type': 'application/json', 'X-Mac-Remote': '1' };
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let data = null;
  try { data = await response.json(); } catch (_) { /* non-JSON error page */ }
  if (!response.ok) {
    const error = new Error((data && data.error) || `HTTP ${response.status}`);
    error.state = data && data.state;
    throw error;
  }
  return data;
}

async function refresh() {
  try {
    render(await call('GET', '/api/state'));
    setConn(true);
  } catch (_) {
    setConn(false);
    return;
  }
  try {
    const [sudo, enroll] = await Promise.all([call('GET', '/api/approvals/sudo'), call('GET', '/api/approvals/enroll')]);
    renderApprovals(sudo.items);
    renderEnroll(enroll.items[0] || null);
  } catch (_) { /* the state call already reported the connection; approvals retry next tick */ }
}

async function act(action, value, quiet = false) {
  try {
    const result = await call('POST', '/api/action', value === undefined ? { action } : { action, value });
    render(result);
    setConn(true);
    if (!result.action.reached) toast(t(UNREACHED[action] || 'unreached'), true);
    else if (!quiet && DONE[action]) toast(t(DONE[action]));
    else if (action === 'mute') toast(value ? t('sound.off') : t('sound.on'));
    return result;
  } catch (error) {
    if (error.state) render(error.state);
    const offline = error instanceof TypeError;
    if (offline) setConn(false);
    toast(offline ? t('conn.failed') : error.message, true);
    return null;
  }
}

function tile(id, { on = false, na = false, alert = false, cap }) {
  const el = $(id);
  el.classList.toggle('on', on);
  el.classList.toggle('na', na);
  el.classList.toggle('alert', alert);
  el.setAttribute('aria-pressed', String(on));
  if (cap !== undefined) el.querySelector('.cap').textContent = cap;
}

async function press(id, run) {
  const el = $(id);
  if (el.classList.contains('wait')) return;
  el.classList.add('wait');
  try { await run(); } finally { el.classList.remove('wait'); }
}

function render(s) {
  state = s;
  $('name').textContent = s.name || 'Mac';

  const b = s.battery;
  if (b) {
    const low = b.percent <= 20 && b.state !== 'charging';
    $('batPct').textContent = b.percent;
    $('batArc').setAttribute('stroke-dasharray', `${b.percent} 100`);
    $('batArc').style.stroke = low ? 'var(--red)' : b.percent <= 35 && b.state !== 'charging' ? 'var(--orange)' : 'var(--green)';
    $('batChip').hidden = false;
    $('batChip').classList.toggle('charging', b.state === 'charging');
    $('batChip').querySelector('.ic').style.display = b.state === 'charging' ? '' : 'none';
    $('batChipText').textContent = BATTERY[b.state] ? t(BATTERY[b.state]) : b.state;
    $('batSub').textContent = SOURCE[b.source] ? t(SOURCE[b.source]) : (b.source || '');
  } else {
    $('batPct').textContent = '--';
    $('batChip').hidden = true;
    $('batSub').textContent = t('battery.none');
  }

  const asleep = s.displays_asleep;
  tile('displayBtn', { on: asleep === false, na: asleep === null, cap: asleep === null ? t('unknown') : asleep ? t('display.off') : t('display.on') });
  tile('lockBtn', { on: s.locked === true, na: s.locked === null, cap: s.locked ? t('locked') : t('lock') });
  $('lockIcon').setAttribute('href', s.locked ? '#i-lock' : '#i-unlock');
  tile('muteBtn', { on: s.muted === true, na: s.muted === null, alert: s.muted === true, cap: s.muted === null ? t('unknown') : s.muted ? t('muted') : t('mute') });
  const keyboardBlocked = asleep === true;
  tile('kbdOffBtn', { on: s.keyboard === 0 && !keyboardBlocked, na: s.keyboard === null || keyboardBlocked,
    cap: keyboardBlocked ? t('display.asleep') : s.keyboard === 0 ? t('keyboard.isoff') : t('keyboard.off') });

  document.querySelectorAll('.slider').forEach((el) => {
    const value = s[el.dataset.key];
    // macOS keeps the keyboard backlight off while the display sleeps and ignores changes.
    el.blocked = el.dataset.key === 'keyboard' && keyboardBlocked ? t('keyboard.blocked') : '';
    el.classList.toggle('na', value === null || el.blocked !== '');
    if (value === null) { el.show(null); return; }
    if (el.blocked) { el.show(0); el.querySelector('.val').textContent = t('display.asleep'); return; }
    if (!busy.has(el.dataset.key)) el.show(value);
  });

  if (!SECURE && s.https_origin) {
    $('secureRow').hidden = false;
    $('secureLink').href = s.https_origin + '/';
  }
  $('seen').textContent = clock.format(new Date());
  $('beat').textContent = s.server && s.server.heartbeat_at ? clock.format(new Date(s.server.heartbeat_at)) : '--';
  const errors = Object.entries(s.errors || {});
  $('errs').hidden = errors.length === 0;
  $('errs').textContent = errors.map(([key, message]) => `${t('field.' + key) === 'field.' + key ? key : t('field.' + key)}: ${message}`).join('\n');
}

function slider(el) {
  const key = el.dataset.key;
  const clamp = (n) => Math.max(0, Math.min(100, n));
  let startY = 0, startValue = 0, value = 0, moved = false, inflight = false, pending = null, timer = 0;
  el.blocked = '';

  el.show = (v) => {
    el.style.setProperty('--v', v === null ? 0 : v);
    el.querySelector('.val').textContent = v === null ? '--' : v;
    el.setAttribute('aria-valuenow', v === null ? 0 : v);
    el.classList.toggle('lit', v !== null && v >= 24);
    el.classList.toggle('top', v !== null && v >= 88);
  };

  async function send(v) {
    if (inflight) { pending = v; return; }
    inflight = true;
    busy.add(key);
    await act(key, v, true);
    inflight = false;
    if (pending !== null && pending !== v) {
      const next = pending;
      pending = null;
      send(next);
      return;
    }
    pending = null;
    if (!el.classList.contains('drag')) {
      busy.delete(key);
      if (state) render(state);
    }
  }

  function schedule() {
    if (timer) return;
    timer = setTimeout(() => { timer = 0; send(value); }, 110);
  }

  el.addEventListener('pointerdown', (event) => {
    if (el.blocked) { toast(el.blocked, true); return; }
    if (!state || state[key] === null) return;
    el.setPointerCapture(event.pointerId);
    el.classList.add('drag');
    busy.add(key);
    startY = event.clientY;
    startValue = value = state[key];
    moved = false;
  });
  el.addEventListener('pointermove', (event) => {
    if (!el.classList.contains('drag')) return;
    const next = clamp(Math.round(startValue + (startY - event.clientY) / el.clientHeight * 100));
    if (next === value) return;
    value = next;
    moved = true;
    el.show(value);
    schedule();
  });
  const end = () => {
    if (!el.classList.contains('drag')) return;
    el.classList.remove('drag');
    clearTimeout(timer);
    timer = 0;
    if (moved) send(value);
    else if (!inflight) busy.delete(key);
  };
  el.addEventListener('pointerup', end);
  el.addEventListener('pointercancel', end);
  el.addEventListener('keydown', (event) => {
    const step = { ArrowUp: 5, ArrowRight: 5, ArrowDown: -5, ArrowLeft: -5 }[event.key];
    if (!step || !state || state[key] === null) return;
    if (el.blocked) { toast(el.blocked, true); return; }
    event.preventDefault();
    value = clamp((busy.has(key) ? value : state[key]) + step);
    el.show(value);
    send(value);
  });
}

document.querySelectorAll('.slider').forEach(slider);

$('displayBtn').addEventListener('click', () => press('displayBtn', async () => {
  if (!state || state.displays_asleep === null) return;
  await act(state.displays_asleep ? 'display_on' : 'display_off');
}));
$('lockBtn').addEventListener('click', () => press('lockBtn', async () => {
  if (state && state.locked) { toast(t('lock.local')); return; }
  await act('lock');
}));
$('awayBtn').addEventListener('click', () => press('awayBtn', () => act('away')));
$('muteBtn').addEventListener('click', () => press('muteBtn', async () => {
  if (!state || state.muted === null) { toast(t('mute.none'), true); return; }
  await act('mute', !state.muted);
}));
$('kbdOffBtn').addEventListener('click', () => press('kbdOffBtn', async () => {
  if (state && state.displays_asleep === true) { toast(t('keyboard.blocked'), true); return; }
  if (!state || state.keyboard === null) return;
  await act('keyboard', 0);
}));

function tick() { $('date').textContent = today.format(new Date()); }
let poll = 0;
