// FamBank – erfasst Standort + Geräte-Zeit für Beweisfotos.
// Füllt versteckte Felder (lat/lon/accuracy/client_time) im Formular [data-geo].
// Hinweis: Geolocation funktioniert nur im sicheren Kontext (HTTPS oder localhost).
(function () {
  function init() {
    var form = document.querySelector('form[data-geo]');
    if (!form) return;

    // Geräte-Zeit immer mitsenden (nur als Hinweis – der Server setzt die maßgebliche Zeit)
    var ct = form.querySelector('input[name="client_time"]');
    if (ct) ct.value = new Date().toISOString();

    var status = document.getElementById('geo-status');
    function setStatus(text, cls) {
      if (status) { status.textContent = text; status.className = 'geo-status ' + (cls || ''); }
    }

    if (!('geolocation' in navigator)) {
      setStatus('Standort auf diesem Gerät nicht verfügbar.', 'warn');
      return;
    }
    setStatus('📍 Standort wird erfasst …', '');
    navigator.geolocation.getCurrentPosition(
      function (pos) {
        var lat = form.querySelector('input[name="lat"]');
        var lon = form.querySelector('input[name="lon"]');
        var acc = form.querySelector('input[name="accuracy"]');
        if (lat) lat.value = pos.coords.latitude;
        if (lon) lon.value = pos.coords.longitude;
        if (acc) acc.value = Math.round(pos.coords.accuracy);
        setStatus('📍 Standort erfasst (±' + Math.round(pos.coords.accuracy) + ' m) ✓', 'ok');
      },
      function (err) {
        setStatus('Standort nicht erfasst: ' + err.message, 'warn');
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
    );
  }

  // Vorschau des gewählten Fotos
  function preview() {
    var input = document.querySelector('input[type="file"][data-preview]');
    if (!input) return;
    input.addEventListener('change', function () {
      var img = document.getElementById('photo-preview');
      if (input.files && input.files[0] && img) {
        img.src = URL.createObjectURL(input.files[0]);
        img.style.display = 'block';
      }
    });
  }

  if (document.readyState !== 'loading') { init(); preview(); }
  else document.addEventListener('DOMContentLoaded', function () { init(); preview(); });
})();
