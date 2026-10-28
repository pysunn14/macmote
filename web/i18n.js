'use strict';
const $ = (id) => document.getElementById(id);
// Strings for the page. English is the default; Korean follows the phone's language or the footer button.
/* ---- language: English by default, Korean when the phone is Korean; a footer button overrides ---- */
const STRINGS = {
  en: {
    'conn.connecting': 'Connecting', 'conn.ok': 'Connected', 'conn.lost': 'Offline', 'conn.failed': 'Could not reach the Mac',
    'approvals.title': 'sudo approval requests', 'approve': 'Approve with Face ID', 'deny': 'Deny', 'denied': 'Denied',
    'approve.sent': 'Approval sent. The Mac is checking the signature', 'approve.gone': 'That sudo request has already ended or expired',
    'approve.meta': '{parent} (pid {ppid}) asked · in {cwd} · user {user} · ', 'approve.left': '{n}s left',
    'passkey.https': 'Passkeys only work on the HTTPS address',
    'enroll.title': 'Register this phone for approvals', 'enroll.help': 'Type the code shown in the Mac terminal and create a passkey with Face ID. User',
    'enroll.code': 'Enrollment code', 'enroll.button': 'Register with Face ID', 'enroll.code.bad': 'Enter the 6-digit enrollment code',
    'enroll.unavailable': 'This device says it cannot create a Face ID passkey. Check that iCloud Keychain is on',
    'enroll.nokey': 'This browser does not return the public key', 'enroll.sent': 'Registration sent. Check the terminal for the result',
    'push.checking': 'Checking', 'push.https': 'HTTPS needed', 'push.home': 'Home Screen app only', 'push.on': 'Turn off', 'push.off': 'Turn on',
    'push.unavailable': 'Notifications are not available here', 'push.disabled': 'Notifications turned off', 'push.denied': 'Notification permission was denied',
    'push.enabled': 'sudo approval notifications turned on', 'push.broken': 'Unavailable',
    'battery': 'Battery', 'battery.reading': 'Reading battery', 'battery.none': 'No battery information',
    'bat.charging': 'Charging', 'bat.charged': 'Charged', 'bat.discharging': 'On battery', 'bat.attached': 'Not charging', 'bat.finishing': 'Finishing charge',
    'src.ac': 'Plugged into power', 'src.battery': 'Running on battery', 'src.ups': 'Running on UPS',
    'display': 'Display', 'display.aria': 'Display on or off', 'display.on': 'On', 'display.off': 'Off', 'unknown': 'Unknown',
    'lock': 'Lock', 'lock.aria': 'Lock the Mac', 'locked': 'Locked', 'lock.local': 'Unlocking has to happen at the Mac',
    'away': 'Step away', 'away.sub': 'Dim, lock, sleep', 'away.aria': 'Step away: keyboard off, lock, display off',
    'brightness': 'Brightness', 'volume': 'Volume', 'keyboard': 'Keyboard backlight', 'keyboard.off': 'Keyboard off', 'keyboard.isoff': 'Off',
    'mute': 'Mute', 'muted': 'Muted', 'mute.none': 'Volume could not be read, so mute cannot be changed', 'sound.off': 'Sound off', 'sound.on': 'Sound on',
    'display.asleep': 'Display off', 'keyboard.blocked': 'The keyboard backlight cannot change while the display is off. Wake the display first',
    'done.display_off': 'Display turned off', 'done.display_on': 'Display turned on', 'done.lock': 'Mac locked',
    'done.away': 'Keyboard off, locked, display off', 'done.keyboard': 'Keyboard backlight turned off',
    'unreached': 'Command sent, but the value has not changed yet', 'unreached.display_off': 'Command sent, but the display is still on',
    'unreached.display_on': 'Command sent, but the display is still off', 'unreached.lock': 'Command sent, but the Mac is not locked yet',
    'unreached.away': 'Command sent, but the display or lock state has not changed yet',
    'field.volume': 'Volume', 'field.muted': 'Mute', 'field.helper': 'Helper', 'field.keyboard': 'Keyboard', 'field.brightness': 'Brightness', 'field.battery': 'Battery',
    'foot': 'Connection', 'foot.seen': 'Last update', 'foot.beat': 'Server heartbeat', 'foot.push': 'sudo approval alerts',
    'foot.secure': 'Passkeys and alerts', 'foot.secure.link': 'Open the HTTPS address', 'foot.language': 'Language', 'lang.name': 'English',
    'setup.title': 'Set up this phone', 'setup.step.browser': 'Open in Safari', 'setup.step.passkey': 'Register with Face ID',
    'setup.step.test': 'Approve the test request', 'setup.step.home': 'Add to the Home Screen', 'setup.step.push': 'Turn on notifications',
    'setup.inapp': 'This is an in-app browser, which cannot create passkeys. Copy the link and open it in Safari.',
    'setup.copy': 'Copy link', 'setup.copied': 'Link copied', 'setup.token': 'Pairing token from the QR code is filled in.',
    'setup.passkey.hint': 'Tap "Register with Face ID" below.',
    'setup.test.hint': 'A test request appears below right after registration. Approve it with Face ID; the Mac verifies the signature and prints the result in the terminal.',
    'setup.home.hint': 'Tap the share button (the square with an arrow) at the bottom of Safari, scroll the sheet and choose "Add to Home Screen". Then open Macmote from the Home Screen to finish.',
    'setup.push.hint': 'Turn on notifications below, then send a test notification.', 'setup.push.test': 'Send test notification',
    'setup.push.sent': 'Test notification sent', 'setup.done': 'Setup complete', 'setup.done.hint': 'sudo approval requests will now reach this phone.',
    'step.done': 'Done', 'step.now': 'Now', 'step.later': 'Next',
  },
  ko: {
    'conn.connecting': '연결 중', 'conn.ok': '연결됨', 'conn.lost': '연결 끊김', 'conn.failed': '맥에 연결하지 못했습니다',
    'approvals.title': 'sudo 승인 요청', 'approve': 'Face ID로 승인', 'deny': '거부', 'denied': '거부했습니다',
    'approve.sent': '승인을 보냈습니다. 맥이 서명을 확인합니다', 'approve.gone': '그 sudo 요청은 이미 끝났거나 시간이 지났습니다',
    'approve.meta': '{parent} (pid {ppid})이(가) 요청 · 디렉터리 {cwd} · 사용자 {user} · ', 'approve.left': '{n}초 남음',
    'passkey.https': 'passkey는 HTTPS 주소에서만 쓸 수 있습니다',
    'enroll.title': '이 폰을 승인 기기로 등록', 'enroll.help': '맥 터미널에 표시된 등록 코드를 입력하고 Face ID로 passkey를 만듭니다. 사용자',
    'enroll.code': '등록 코드', 'enroll.button': 'Face ID로 등록', 'enroll.code.bad': '6자리 등록 코드를 입력하세요',
    'enroll.unavailable': '이 기기에서 Face ID passkey를 쓸 수 없다고 합니다. iCloud 키체인이 켜져 있는지 확인하세요',
    'enroll.nokey': '이 브라우저는 공개키를 내주지 않습니다', 'enroll.sent': '등록 정보를 보냈습니다. 터미널에서 결과를 확인하세요',
    'push.checking': '확인 중', 'push.https': 'HTTPS 필요', 'push.home': '홈 화면 앱에서', 'push.on': '끄기', 'push.off': '켜기',
    'push.unavailable': '이 환경에서는 알림을 쓸 수 없습니다', 'push.disabled': '알림을 껐습니다', 'push.denied': '알림 권한이 거부되었습니다',
    'push.enabled': 'sudo 승인 알림을 켰습니다', 'push.broken': '사용 불가',
    'battery': '배터리', 'battery.reading': '배터리 정보를 읽는 중', 'battery.none': '배터리 정보 없음',
    'bat.charging': '충전 중', 'bat.charged': '충전 완료', 'bat.discharging': '배터리 사용', 'bat.attached': '충전 안 함', 'bat.finishing': '충전 마무리',
    'src.ac': '전원 어댑터에 연결됨', 'src.battery': '배터리로 동작 중', 'src.ups': 'UPS 전원으로 동작 중',
    'display': '화면', 'display.aria': '화면 켜고 끄기', 'display.on': '켜짐', 'display.off': '꺼짐', 'unknown': '알 수 없음',
    'lock': '잠금', 'lock.aria': '잠그기', 'locked': '잠김', 'lock.local': '잠금 해제는 맥에서 직접 해야 합니다',
    'away': '자리 비움', 'away.sub': '끄고 잠그기', 'away.aria': '자리 비움: 키보드를 끄고 잠근 뒤 화면 끄기',
    'brightness': '화면 밝기', 'volume': '음량', 'keyboard': '키보드 불빛', 'keyboard.off': '키보드 끄기', 'keyboard.isoff': '꺼짐',
    'mute': '음소거', 'muted': '음소거됨', 'mute.none': '음량 정보를 읽지 못해 음소거를 바꿀 수 없습니다', 'sound.off': '소리를 껐습니다', 'sound.on': '소리를 켰습니다',
    'display.asleep': '화면 꺼짐', 'keyboard.blocked': '화면이 꺼져 있으면 키보드 불빛을 바꿀 수 없습니다. 화면을 먼저 켜 주세요',
    'done.display_off': '화면을 껐습니다', 'done.display_on': '화면을 켰습니다', 'done.lock': '맥을 잠갔습니다',
    'done.away': '키보드를 끄고 잠근 뒤 화면을 껐습니다', 'done.keyboard': '키보드 불빛을 껐습니다',
    'unreached': '명령은 보냈지만 값이 아직 바뀌지 않았습니다', 'unreached.display_off': '명령은 보냈지만 화면이 아직 켜져 있습니다',
    'unreached.display_on': '명령은 보냈지만 화면이 아직 꺼져 있습니다', 'unreached.lock': '명령은 보냈지만 아직 잠기지 않았습니다',
    'unreached.away': '명령은 보냈지만 화면이나 잠금 상태가 아직 바뀌지 않았습니다',
    'field.volume': '음량', 'field.muted': '음소거', 'field.helper': '도우미', 'field.keyboard': '키보드', 'field.brightness': '밝기', 'field.battery': '배터리',
    'foot': '연결 상태', 'foot.seen': '마지막 확인', 'foot.beat': '서버 하트비트', 'foot.push': 'sudo 승인 알림',
    'foot.secure': 'passkey와 알림', 'foot.secure.link': 'HTTPS 주소에서 열기', 'foot.language': '언어', 'lang.name': '한국어',
    'setup.title': '이 폰 설정', 'setup.step.browser': 'Safari에서 열기', 'setup.step.passkey': 'Face ID로 등록',
    'setup.step.test': '시험 요청 승인', 'setup.step.home': '홈 화면에 추가', 'setup.step.push': '알림 켜기',
    'setup.inapp': '앱 안의 브라우저라 passkey를 만들 수 없습니다. 링크를 복사해 Safari에서 여세요.',
    'setup.copy': '링크 복사', 'setup.copied': '링크를 복사했습니다', 'setup.token': 'QR 코드의 페어링 토큰이 입력되어 있습니다.',
    'setup.passkey.hint': '아래 "Face ID로 등록"을 누르세요.',
    'setup.test.hint': '등록 직후 아래에 시험 요청이 뜹니다. Face ID로 승인하면 맥이 서명을 검증하고 터미널에 결과를 보여 줍니다.',
    'setup.home.hint': 'Safari 아래의 공유 버튼(네모에서 화살표가 올라가는 모양)을 누르고, 메뉴를 위로 올려 "홈 화면에 추가"를 고릅니다. 그다음 홈 화면의 Macmote를 열어 마무리합니다.',
    'setup.push.hint': '아래에서 알림을 켠 뒤 시험 알림을 보냅니다.', 'setup.push.test': '시험 알림 보내기',
    'setup.push.sent': '시험 알림을 보냈습니다', 'setup.done': '설정 완료', 'setup.done.hint': '이제 sudo 승인 요청이 이 폰으로 옵니다.',
    'step.done': '완료', 'step.now': '지금', 'step.later': '다음',
  },
};
function pickLanguage() {
  try { const saved = localStorage.getItem('lang'); if (STRINGS[saved]) return saved; } catch (_) { /* storage may be unavailable */ }
  return (navigator.language || 'en').toLowerCase().startsWith('ko') ? 'ko' : 'en';
}
let LANG = pickLanguage();
const t = (key, vars) => {
  let text = Object.prototype.hasOwnProperty.call(STRINGS[LANG], key) ? STRINGS[LANG][key] : (STRINGS.en[key] || key);
  if (vars) for (const [name, value] of Object.entries(vars)) text = text.split(`{${name}}`).join(value);
  return text;
};
const LOCALE = { en: 'en-US', ko: 'ko-KR' };
let clock, today;
function applyLanguage() {
  document.documentElement.lang = LANG;
  clock = new Intl.DateTimeFormat(LOCALE[LANG], { hour: 'numeric', minute: '2-digit', second: '2-digit' });
  today = new Intl.DateTimeFormat(LOCALE[LANG], { month: 'long', day: 'numeric', weekday: 'long' });
  document.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-aria]').forEach((el) => { el.setAttribute('aria-label', t(el.dataset.i18nAria)); });
  document.querySelectorAll('[data-i18n-placeholder]').forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  $('langBtn').textContent = t('lang.name');
}
$('langBtn').addEventListener('click', () => {
  LANG = LANG === 'ko' ? 'en' : 'ko';
  try { localStorage.setItem('lang', LANG); } catch (_) { /* fine without memory */ }
  applyLanguage();
  if (state) render(state);
  if (typeof renderSetup === 'function') renderSetup();
  showPush().catch(() => {});
});
