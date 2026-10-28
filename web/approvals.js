'use strict';
// sudo approvals, passkey enrollment and Web Push: everything that needs the HTTPS origin.
/* ---- sudo approvals: the phone signs the module's challenge with a passkey ---- */
const b64u = (buffer) => btoa(String.fromCharCode(...new Uint8Array(buffer))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
function unb64u(text) {
  let s = text.replace(/-/g, '+').replace(/_/g, '/');
  while (s.length % 4) s += '=';
  return Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
}
function report(where, error) {
  // Fire-and-forget: the server log is the only place these phone-side errors can be read.
  call('POST', '/api/log', { where, name: error && error.name, message: error && error.message, ua: navigator.userAgent,
    standalone: !!navigator.standalone, secure: SECURE, host: location.host }).catch(() => {});
}
const describe = (error) => (error && error.name ? `${error.name}: ${error.message}` : String(error));
const deciding = new Set();
const relative = new Intl.RelativeTimeFormat('ko-KR', { numeric: 'always' });

// Opened from a notification: say so when that request is already gone instead of showing nothing.
let expectedApproval = new URLSearchParams(location.search).get('approval');

function renderApprovals(items) {
  const box = $('approvals');
  const list = $('approvalList');
  if (expectedApproval) {
    if (!items.some((i) => i.id === expectedApproval)) toast(t('approve.gone'), true);
    expectedApproval = null;
    history.replaceState(null, '', location.pathname);
  }
  const ids = new Set(items.map((i) => i.id));
  for (const el of [...list.children]) if (!ids.has(el.dataset.id)) el.remove();
  for (const item of items) {
    let el = list.querySelector(`[data-id="${item.id}"]`);
    if (!el) {
      el = document.createElement('div');
      el.className = 'approval';
      el.dataset.id = item.id;
      el.innerHTML = '<div class="cmd"></div><div class="meta"><span class="who"></span><span class="left"></span></div><div class="actions">'
        + '<button class="btn no"></button><button class="btn ok"></button></div>';
      el.querySelector('.cmd').textContent = item.command.join(' ');
      el.querySelector('.btn.no').textContent = t('deny');
      el.querySelector('.btn.ok').textContent = t('approve');
      el.querySelector('.who').textContent = t('approve.meta', { parent: item.parent, ppid: item.ppid, cwd: item.cwd, user: item.user });
      el.querySelector('.btn.no').addEventListener('click', () => decide(item, false, el));
      el.querySelector('.btn.ok').addEventListener('click', () => decide(item, true, el));
      list.appendChild(el);
    }
    const left = Math.max(0, Math.round((new Date(item.created_at).getTime() + item.timeout * 1000 - Date.now()) / 1000));
    el.querySelector('.left').textContent = t('approve.left', { n: left });
    if (!SECURE) el.querySelector('.btn.ok').disabled = true;
  }
  box.hidden = items.length === 0;
}

async function decide(item, approve, el) {
  if (deciding.has(item.id)) return;
  deciding.add(item.id);
  el.querySelectorAll('.btn').forEach((b) => { b.disabled = true; });
  try {
    if (!approve) {
      await call('POST', `/api/approvals/sudo/${item.id}/deny`, {});
      toast(t('denied'));
    } else {
      if (!SECURE) throw new Error(t('passkey.https'));
      const credential = await navigator.credentials.get({ publicKey: {
        challenge: unb64u(item.challenge), rpId: location.hostname, userVerification: 'required', timeout: 60000 } });
      const r = credential.response;
      await call('POST', `/api/approvals/sudo/${item.id}/answer`, {
        credential_id: b64u(credential.rawId), authenticator_data: b64u(r.authenticatorData),
        client_data_json: b64u(r.clientDataJSON), signature: b64u(r.signature) });
      toast(t('approve.sent'));
      if (item.parent.startsWith('macmote-enroll')) { setup.tested = true; saveSetup(); renderSetup(); }
    }
    el.remove();
    $('approvals').hidden = $('approvalList').children.length === 0;
  } catch (error) {
    report('approve', error);
    toast(describe(error), true);
    el.querySelectorAll('.btn').forEach((b) => { b.disabled = false; });
  } finally {
    deciding.delete(item.id);
  }
}

/* ---- setup wizard: driven by the pairing token in the URL fragment, then by what is left ----
   Safari and the Home Screen app keep separate storage on iOS, so the passkey is created in
   Safari (where the QR link opens) and notifications are turned on in the Home Screen app. */
const STANDALONE = !!navigator.standalone;
const IN_APP = /KAKAOTALK|FBAN|FBAV|Instagram|Line\/|NAVER|Twitter|Snapchat|wv\)/i.test(navigator.userAgent);
let pairToken = (location.hash.match(/#pair=([A-Za-z0-9_-]+)/) || [])[1] || null;
if (pairToken) {
  try { localStorage.setItem('pairToken', pairToken); } catch (_) { /* fine */ }
  history.replaceState(null, '', location.pathname);
} else {
  try { pairToken = localStorage.getItem('pairToken'); } catch (_) { /* fine */ }
}
const setup = { enrolled: false, tested: false, pushed: false };
try { Object.assign(setup, JSON.parse(localStorage.getItem('setup') || '{}')); } catch (_) { /* fine */ }
function saveSetup() { try { localStorage.setItem('setup', JSON.stringify(setup)); } catch (_) { /* fine */ } }

function setupStep() {
  if (!STANDALONE) {
    if (IN_APP) return 'browser';
    if (pairToken && !setup.enrolled) return 'passkey';
    if (setup.enrolled && !setup.tested) return 'test';
    if (setup.enrolled) return 'home';
    return null;
  }
  return setup.pushed ? null : 'push';
}
const STEPS = ['browser', 'passkey', 'test', 'home', 'push'];
function renderSetup() {
  const step = setupStep();
  $('setup').hidden = step === null;
  if (step === null) return;
  const index = STEPS.indexOf(step);
  $('steps').innerHTML = '';
  STEPS.forEach((name, i) => {
    const li = document.createElement('li');
    li.className = i < index ? 'done' : i === index ? 'now' : '';
    li.innerHTML = `<span class="n">${i < index ? '✓' : i + 1}</span><span></span>`;
    li.querySelector('span:last-child').textContent = t('setup.step.' + name);
    $('steps').appendChild(li);
  });
  const hint = $('setupHint');
  const actions = $('setupActions');
  actions.innerHTML = '';
  const button = (label, cls, handler) => {
    const b = document.createElement('button');
    b.className = 'btn ' + cls;
    b.textContent = label;
    b.addEventListener('click', handler);
    actions.appendChild(b);
    return b;
  };
  if (step === 'browser') {
    hint.textContent = t('setup.inapp');
    button(t('setup.copy'), 'ok', async () => {
      const link = location.origin + '/' + (pairToken ? '#pair=' + pairToken : '');
      try { await navigator.clipboard.writeText(link); toast(t('setup.copied')); } catch (_) { prompt('', link); }
    });
  } else if (step === 'passkey') {
    hint.textContent = t('setup.passkey.hint');
  } else if (step === 'test') {
    hint.textContent = t('setup.test.hint');
  } else if (step === 'home') {
    hint.innerHTML = '';
    hint.append(t('setup.home.hint'));
  } else if (step === 'push') {
    hint.textContent = t('setup.push.hint');
    button(t('setup.push.test'), 'ok', async () => {
      try {
        await call('POST', '/api/push/test', {});
        toast(t('setup.push.sent'));
        setup.pushed = true;
        saveSetup();
        renderSetup();
      } catch (error) { toast(describe(error), true); }
    });
  }
}

/* ---- enrollment: create a passkey and send its public key back with the token or typed code ---- */
let enrolling = null;
function renderEnroll(item) {
  enrolling = item;
  $('enroll').hidden = item === null || setupStep() === 'browser';
  if (item) {
    $('enrollUser').textContent = item.user;
    $('enrollCode').hidden = !!pairToken;
    $('enrollToken').hidden = !pairToken;
  }
  renderSetup();
}
$('enrollBtn').addEventListener('click', async () => {
  const item = enrolling;
  const code = pairToken || $('enrollCode').value.trim();
  if (!item) return;
  if (!pairToken && !/^\d{6}$/.test(code)) { toast(t('enroll.code.bad'), true); return; }
  if (!SECURE || !window.PublicKeyCredential) { toast(t('passkey.https'), true); return; }
  $('enrollBtn').disabled = true;
  try {
    if (!(await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable())) {
      throw new Error(t('enroll.unavailable'));
    }
    const credential = await navigator.credentials.create({ publicKey: {
      challenge: unb64u(item.challenge), rp: { id: location.hostname, name: 'Macmote' },
      user: { id: new TextEncoder().encode(`${item.user}@${location.hostname}`), name: item.user, displayName: `${item.user} (Macmote)` },
      pubKeyCredParams: [{ type: 'public-key', alg: -7 }],
      authenticatorSelection: { authenticatorAttachment: 'platform', residentKey: 'required', userVerification: 'required' },
      attestation: 'none', timeout: 120000 } });
    const spki = credential.response.getPublicKey();
    if (!spki) throw new Error(t('enroll.nokey'));
    await call('POST', `/api/approvals/enroll/${item.id}/answer`, {
      code, credential_id: b64u(credential.rawId), public_key: b64u(spki), client_data_json: b64u(credential.response.clientDataJSON) });
    toast(t('enroll.sent'));
    $('enrollCode').value = '';
    setup.enrolled = true;
    saveSetup();
    pairToken = null;
    try { localStorage.removeItem('pairToken'); } catch (_) { /* fine */ }
    renderEnroll(null);
  } catch (error) {
    report('enroll', error);
    toast(describe(error), true);
  } finally {
    $('enrollBtn').disabled = false;
  }
});

/* ---- push: only a Home Screen app on the HTTPS origin can subscribe on iOS ---- */
async function pushRegistration() {
  if (!SECURE || !('serviceWorker' in navigator) || !('PushManager' in window)) return null;
  return navigator.serviceWorker.register('sw.js');
}
async function showPush() {
  const btn = $('pushBtn');
  if (!SECURE) { btn.textContent = t('push.https'); btn.disabled = true; return; }
  if (!('PushManager' in window)) { btn.textContent = t('push.home'); btn.disabled = true; return; }
  const registration = await pushRegistration();
  const subscription = registration && await registration.pushManager.getSubscription();
  btn.textContent = subscription ? t('push.on') : t('push.off');
  btn.dataset.on = subscription ? '1' : '';
  btn.disabled = false;
}
$('pushBtn').addEventListener('click', async () => {
  const btn = $('pushBtn');
  btn.disabled = true;
  try {
    const registration = await pushRegistration();
    if (!registration) throw new Error(t('push.unavailable'));
    const current = await registration.pushManager.getSubscription();
    if (current) {
      await call('POST', '/api/push/unsubscribe', { endpoint: current.endpoint });
      await current.unsubscribe();
      toast(t('push.disabled'));
    } else {
      if (await Notification.requestPermission() !== 'granted') throw new Error(t('push.denied'));
      const { public_key } = await call('GET', '/api/push/key');
      const subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: unb64u(public_key) });
      await call('POST', '/api/push/subscribe', subscription.toJSON());
      toast(t('push.enabled'));
    }
  } catch (error) {
    report('push', error);
    toast(describe(error), true);
  }
  await showPush();
});

/* ---- boot ---- */
renderSetup();
function start() { stop(); tick(); refresh(); poll = setInterval(() => { tick(); refresh(); }, 3000); }
applyLanguage();
showPush().catch(() => { $('pushBtn').textContent = t('push.broken'); $('pushBtn').disabled = true; });
function stop() { clearInterval(poll); poll = 0; }
document.addEventListener('visibilitychange', () => (document.hidden ? stop() : start()));
start();
