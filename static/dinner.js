/* LevelUp – Dinner countdown + Surprise card logic */

/* ---- Dinner countdown ---- */
function _dinnerSecsLeft(timeStr) {
  if (!timeStr) return null;
  const [h, m] = timeStr.split(':').map(Number);
  const now = new Date();
  const target = new Date(now);
  target.setHours(h, m, 0, 0);
  return Math.floor((target - now) / 1000);
}

function _renderCountdown(el, secs) {
  if (secs === null) { el.textContent = ''; return; }
  if (secs <= 0) { el.textContent = '🍴 Jetzt Abendessen!'; el.className = el.className + ' now'; return; }
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  el.textContent = h > 0
    ? `⏰ Noch ${h}h ${m}m`
    : m > 0
      ? `⏰ Noch ${m}m ${String(s).padStart(2,'0')}s`
      : `⏰ Noch ${s}s`;
}

function _startDinnerCountdown(elId, timeStr) {
  const el = document.getElementById(elId);
  if (!el || !timeStr) return;
  function tick() {
    _renderCountdown(el, _dinnerSecsLeft(timeStr));
  }
  tick();
  setInterval(tick, 1000);
}

document.addEventListener('DOMContentLoaded', function () {
  // Child page
  const child = document.getElementById('dinnerCountdown');
  if (child) _startDinnerCountdown('dinnerCountdown', child.dataset.time);
  // Admin page
  const admin = document.getElementById('adminDinnerCountdown');
  if (admin) _startDinnerCountdown('adminDinnerCountdown', admin.dataset.time);
});

/* ---- Rule detail flip ---- */
const _flippedRules = new Set();

function flipRuleDetail(ruleId, event) {
  if (_flippedRules.has(ruleId)) return;
  const card = document.getElementById('rc-' + ruleId);
  if (!card) return;
  card.classList.add('card-flipping');
  setTimeout(function () {
    document.getElementById('rf-' + ruleId).style.display = 'none';
    const back = document.getElementById('rb-' + ruleId);
    back.style.display = 'flex';
    _flippedRules.add(ruleId);
    card.classList.remove('card-flipping');
  }, 200);
}

function flipRuleBack(ruleId, event) {
  event.stopPropagation();
  const card = document.getElementById('rc-' + ruleId);
  if (!card) return;
  card.classList.add('card-flipping');
  setTimeout(function () {
    document.getElementById('rb-' + ruleId).style.display = 'none';
    document.getElementById('rf-' + ruleId).style.display = 'flex';
    _flippedRules.delete(ruleId);
    card.classList.remove('card-flipping');
  }, 200);
}

/* ---- Surprise card ---- */
const _surTimers = {};

function flipSurprise(ruleId, event) {
  event.preventDefault();
  event.stopPropagation();
  const card = document.getElementById('sur-' + ruleId);
  const mystery = card.querySelector('.sur-mystery-face');
  const reveal = document.getElementById('sur-rev-' + ruleId);
  if (!mystery || !reveal) return;

  card.classList.add('sur-flipping');
  setTimeout(function () {
    mystery.style.display = 'none';
    reveal.style.display = 'flex';
    card.classList.remove('sur-flipping');
    _startSurTimer(ruleId);
  }, 220);
}

function flipBack(ruleId, event) {
  if (event) { event.preventDefault(); event.stopPropagation(); }
  clearSurTimer(ruleId);
  const card = document.getElementById('sur-' + ruleId);
  const mystery = card.querySelector('.sur-mystery-face');
  const reveal = document.getElementById('sur-rev-' + ruleId);
  card.classList.add('sur-flipping');
  setTimeout(function () {
    mystery.style.display = '';
    reveal.style.display = 'none';
    card.classList.remove('sur-flipping');
  }, 220);
}

function _startSurTimer(ruleId) {
  let secs = 30;
  const timerEl = document.getElementById('sut-' + ruleId);
  const fillEl  = document.getElementById('suf-' + ruleId);
  if (timerEl) timerEl.textContent = secs;
  if (fillEl)  fillEl.style.width = '100%';

  _surTimers[ruleId] = setInterval(function () {
    secs--;
    if (timerEl) timerEl.textContent = secs;
    if (fillEl)  fillEl.style.width = (secs / 30 * 100) + '%';
    if (secs <= 0) {
      clearSurTimer(ruleId);
      flipBack(ruleId, null);
    }
  }, 1000);
}

function clearSurTimer(ruleId) {
  if (_surTimers[ruleId]) {
    clearInterval(_surTimers[ruleId]);
    delete _surTimers[ruleId];
  }
}
