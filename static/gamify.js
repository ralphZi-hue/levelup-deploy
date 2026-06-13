// FamBank – Gamification für die Kinder-Seite.
// Zählt den Kontostand hoch/runter seit dem letzten Besuch, spielt einen "Pling",
// und feiert mit Konfetti, wenn der Betrag steigt oder ein Level erreicht wird.
(function () {
  var hero = document.getElementById('hero');
  var el = document.getElementById('balanceAmount');
  if (!hero || !el) return;

  var uid = hero.getAttribute('data-uid') || '0';
  var current = parseInt(el.getAttribute('data-cents'), 10) || 0;
  var level = parseInt(hero.getAttribute('data-level'), 10) || 1;
  var keyBal = 'fambank_bal_' + uid;
  var keyLvl = 'fambank_lvl_' + uid;
  var keyMute = 'fambank_mute';

  var prev = parseInt(localStorage.getItem(keyBal), 10);
  if (isNaN(prev)) prev = current;            // erster Besuch: nicht animieren
  var prevLevel = parseInt(localStorage.getItem(keyLvl), 10);
  if (isNaN(prevLevel)) prevLevel = level;

  function fmt(cents) {
    var s = cents < 0 ? '-' : '';
    var v = Math.abs(cents);
    return s + Math.floor(v / 100) + ',' + ('0' + (v % 100)).slice(-2) + ' €';
  }

  // ---- Ton (Web Audio, kein Audio-File nötig) ----
  var muted = localStorage.getItem(keyMute) === '1';
  var toggle = document.getElementById('soundToggle');
  if (toggle) {
    toggle.textContent = muted ? '🔇' : '🔊';
    toggle.addEventListener('click', function () {
      muted = !muted;
      localStorage.setItem(keyMute, muted ? '1' : '0');
      toggle.textContent = muted ? '🔇' : '🔊';
      if (!muted) blip(880, 0.06);
    });
  }
  var actx = null;
  function audio() {
    if (muted) return null;
    try { actx = actx || new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { return null; }
    if (actx.state === 'suspended') actx.resume();
    return actx;
  }
  function blip(freq, dur) {
    var a = audio(); if (!a) return;
    var o = a.createOscillator(), g = a.createGain();
    o.type = 'sine'; o.frequency.value = freq;
    g.gain.setValueAtTime(0.0001, a.currentTime);
    g.gain.exponentialRampToValueAtTime(0.25, a.currentTime + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, a.currentTime + dur);
    o.connect(g); g.connect(a.destination);
    o.start(); o.stop(a.currentTime + dur);
  }
  function pling() { blip(660, 0.08); setTimeout(function () { blip(990, 0.12); }, 90); }
  function fanfare() {
    [523, 659, 784, 1047].forEach(function (f, i) { setTimeout(function () { blip(f, 0.16); }, i * 110); });
  }

  // ---- Konfetti ----
  function confetti(big) {
    var n = big ? 90 : 36;
    var emojis = big ? ['🎉', '⭐', '🏆', '💎', '🌟'] : ['✨', '⭐', '💰', '🎉'];
    for (var i = 0; i < n; i++) {
      (function (i) {
        var s = document.createElement('div');
        s.className = 'confetti';
        s.textContent = emojis[i % emojis.length];
        s.style.left = Math.random() * 100 + 'vw';
        s.style.fontSize = (14 + Math.random() * 18) + 'px';
        s.style.animationDuration = (1.6 + Math.random() * 1.4) + 's';
        s.style.animationDelay = (Math.random() * 0.3) + 's';
        document.body.appendChild(s);
        setTimeout(function () { s.remove(); }, 3200);
      })(i);
    }
  }

  // ---- Hochzähl-Animation ----
  function countUp(from, to, ms) {
    var start = performance.now();
    var rising = to > from;
    function step(now) {
      var p = Math.min(1, (now - start) / ms);
      var eased = 1 - Math.pow(1 - p, 3);           // ease-out
      var val = Math.round(from + (to - from) * eased);
      el.textContent = fmt(val);
      el.classList.toggle('neg', val < 0);
      if (rising && Math.random() < 0.5) { /* nichts */ }
      if (p < 1) requestAnimationFrame(step);
      else el.textContent = fmt(to);
    }
    el.classList.add('pop');
    setTimeout(function () { el.classList.remove('pop'); }, 600);
    requestAnimationFrame(step);
  }

  function run() {
    if (current !== prev) {
      countUp(prev, current, 1200);
      if (current > prev) { pling(); confetti(false); }
    }
    if (level > prevLevel) {
      setTimeout(function () {
        hero.classList.add('levelup');
        fanfare(); confetti(true);
      }, current !== prev ? 700 : 0);
    }
    localStorage.setItem(keyBal, String(current));
    localStorage.setItem(keyLvl, String(level));
  }

  // Browser blockieren Audio teils bis zur ersten Interaktion – Animation läuft trotzdem.
  if (document.readyState !== 'loading') run();
  else document.addEventListener('DOMContentLoaded', run);
})();
