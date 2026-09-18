/* Calendario de turnos: apertura de diálogos y confirmación de la reserva.
   JS vanilla, sin dependencias. */

'use strict';

(() => {
  const bookDialog = document.getElementById('bookDialog');
  const bookForm = document.getElementById('bookForm');
  const slotLabel = document.getElementById('bookSlotLabel');
  const errorBox = document.getElementById('bookError');
  const submitBtn = document.getElementById('bookSubmit');

  let selectedSlot = null;
  let lookupTimer = null;
  let lookupSeq = 0;

  /* ---------- Diálogos genéricos (info / horarios) ---------- */
  document.querySelectorAll('[data-dialog-open]').forEach((trigger) => {
    trigger.addEventListener('click', () => {
      const dialog = document.getElementById(trigger.dataset.dialogOpen);
      if (dialog) dialog.showModal();
    });
  });

  document.querySelectorAll('[data-dialog-close]').forEach((button) => {
    button.addEventListener('click', () => button.closest('dialog')?.close());
  });

  // Click en el fondo oscuro cierra el diálogo
  document.querySelectorAll('dialog').forEach((dialog) => {
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) dialog.close();
    });
  });

  /* ---------- Selección de horario ---------- */
  document.querySelectorAll('.slot--free').forEach((button) => {
    button.addEventListener('click', () => {
      if (!window.IS_LOGGED_IN) {
        window.location.href = window.LOGIN_URL;
        return;
      }
      if (!window.PROFILE_COMPLETE) {
        window.location.href = window.PROFILE_URL;
        return;
      }

      selectedSlot = button.dataset.slot;
      slotLabel.textContent = button.dataset.label;
      hideError();
      bookForm.reset();
      resetAircraftPick();
      clearFuelUi();
      bookDialog.showModal();
    });
  });

  /* ---------- Prefill desde Mis Aeronaves + lookup maestro ---------- */
  const aircraftPick = document.getElementById('aircraft_pick');
  const fuelHint = document.getElementById('aircraftFuelHint');
  const typeHint = document.getElementById('aircraftTypeHint');
  const fuelKnownBlock = document.getElementById('fuelKnownBlock');
  const combustibleDisplay = document.getElementById('combustible_display');
  const primeraCargaWarn = document.getElementById('primeraCargaWarn');
  const aircraftInput = bookForm ? bookForm.aircraft : null;

  const fuelBannerMount = document.getElementById('fuelBannerMount');

  function clearFuelBanner() {
    if (!fuelBannerMount) return;
    fuelBannerMount.hidden = true;
    fuelBannerMount.innerHTML = '';
  }

  function showFuelBanner({ combustible, matricula, tipo, locked }) {
    if (!fuelBannerMount || !window.FuelBanner) return;
    const fuel = combustible || window.AGENDA_PRODUCT || '';
    if (!fuel) { clearFuelBanner(); return; }
    fuelBannerMount.innerHTML = window.FuelBanner.render({
      combustible: fuel,
      matricula: matricula || '',
      tipo: tipo || '',
      audience: 'cliente',
      variant: locked ? 'maestro' : 'turno',
      lockedByMaestro: !!locked,
    });
    fuelBannerMount.hidden = !fuelBannerMount.innerHTML;
  }

  function clearFuelUi() {
    if (fuelHint) {
      fuelHint.hidden = true;
      fuelHint.textContent = '';
    }
    if (typeHint) {
      typeHint.hidden = true;
      typeHint.textContent = '';
    }
    if (fuelKnownBlock) fuelKnownBlock.hidden = true;
    if (combustibleDisplay) combustibleDisplay.value = '';
    if (primeraCargaWarn) primeraCargaWarn.hidden = true;
    clearFuelBanner();
  }

  function resetAircraftPick() {
    if (aircraftPick) aircraftPick.value = '';
    clearFuelUi();
  }

  function applyLookupResult(data, { fromPick } = {}) {
    clearFuelUi();
    if (!data || !data.ok) return;

    const unknown = !!(data.primera_carga || data.unknown_matricula);
    const tipoAvion = (data.modelo || data.tipo || '').trim();
    const mat = (data.matricula || (aircraftInput && aircraftInput.value) || '').trim();

    if (!unknown && data.combustible) {
      if (fuelKnownBlock) fuelKnownBlock.hidden = false;
      if (combustibleDisplay) combustibleDisplay.value = data.combustible;
      if (fuelHint) {
        fuelHint.textContent = 'El combustible sale de la matrícula. Si no coincide, avisá en planta.';
        fuelHint.hidden = false;
      }
      showFuelBanner({
        combustible: data.combustible,
        matricula: mat,
        tipo: tipoAvion,
        locked: true,
      });
    } else {
      if (primeraCargaWarn) primeraCargaWarn.hidden = false;
      if (fuelHint) {
        fuelHint.textContent = fromPick
          ? 'Es la primera vez que cargamos esta matrícula. En planta van a confirmar el combustible con vos.'
          : 'Es la primera vez que cargamos esta matrícula. En planta van a confirmar el combustible con vos.';
        fuelHint.hidden = false;
      }
      // Grado de la agenda (producto) + alert warn debajo
      showFuelBanner({
        combustible: window.AGENDA_PRODUCT || '',
        matricula: mat,
        tipo: tipoAvion,
        locked: false,
      });
    }

    if (tipoAvion && bookForm && bookForm.aircraft_model) {
      // Maestro manda: rellenar / refrescar modelo desde Avion
      bookForm.aircraft_model.value = tipoAvion;
    }
    if (typeHint) {
      if (tipoAvion) {
        typeHint.textContent = 'Tipo: ' + tipoAvion;
        typeHint.hidden = false;
      } else {
        typeHint.hidden = true;
        typeHint.textContent = '';
      }
    }
  }

  function normalizeMatricula(raw) {
    if (window.YPFNormalizeMatricula) return window.YPFNormalizeMatricula(raw);
    return String(raw || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  }

  async function lookupMatricula(raw, opts) {
    const value = normalizeMatricula(raw);
    if (value.length < 2) {
      clearFuelUi();
      return;
    }
    const seq = ++lookupSeq;
    if (fuelHint) {
      fuelHint.textContent = 'Buscando matrícula…';
      fuelHint.hidden = false;
    }
    try {
      const response = await fetch('/api/matricula/' + encodeURIComponent(value), {
        headers: { Accept: 'application/json' },
      });
      if (seq !== lookupSeq) return;
      if (response.status === 401) {
        clearFuelUi();
        return;
      }
      const data = await response.json().catch(() => null);
      if (seq !== lookupSeq) return;
      applyLookupResult(data, opts);
    } catch (_) {
      if (seq !== lookupSeq) return;
      clearFuelUi();
    }
  }

  function scheduleLookup(raw, opts) {
    if (lookupTimer) clearTimeout(lookupTimer);
    lookupTimer = setTimeout(() => lookupMatricula(raw, opts), 350);
  }

  function applyAircraftPick() {
    if (!aircraftPick || !bookForm) return;
    const opt = aircraftPick.selectedOptions[0];
    if (!opt || !opt.value) {
      clearFuelUi();
      return;
    }
    const matricula = opt.dataset.matricula || '';
    const modelo = opt.dataset.modelo || '';
    if (matricula) bookForm.aircraft.value = normalizeMatricula(matricula);
    if (modelo) bookForm.aircraft_model.value = modelo;
    // El maestro manda: lookup confirma combustible readonly / primera carga
    lookupMatricula(matricula, { fromPick: true });
  }

  if (aircraftPick) {
    aircraftPick.addEventListener('change', applyAircraftPick);
  }

  if (aircraftInput) {
    aircraftInput.addEventListener('input', () => {
      if (aircraftPick && aircraftPick.value) aircraftPick.value = '';
      scheduleLookup(aircraftInput.value, { fromPick: false });
    });
    aircraftInput.addEventListener('blur', () => {
      lookupMatricula(aircraftInput.value, { fromPick: false });
    });
  }

  /* ---------- Envío de la reserva ---------- */
  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
  }

  function hideError() {
    errorBox.hidden = true;
    errorBox.textContent = '';
  }

  if (bookForm) {
    bookForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!selectedSlot) return;
      hideError();

      // required + min/max en el HTML ya bloquean el submit si falta algo:
      // el navegador ni dispara este listener si el form no es válido.
      const aircraft = bookForm.aircraft.value.trim();
      const aircraftConfirm = bookForm.aircraft_confirm.value.trim();
      const norm = (v) => (v || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
      if (norm(aircraft) !== norm(aircraftConfirm)) {
        showError('La matrícula de confirmación no coincide. Reescribila exactamente.');
        return;
      }

      const payload = {
        slug: window.AGENDA_SLUG,
        starts_at: selectedSlot,
        aircraft: aircraft,
        aircraft_confirm: aircraftConfirm,
        aircraft_model: bookForm.aircraft_model.value.trim(),
        flight_number: bookForm.flight_number.value.trim(),
        liters: Number(bookForm.liters.value.trim()),
        notes: bookForm.notes.value.trim()
      };

      submitBtn.disabled = true;
      submitBtn.textContent = 'Confirmando…';

      try {
        const response = await fetch('/api/bookings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });

        if (response.status === 401) {
          window.location.href = window.LOGIN_URL;
          return;
        }
        if (response.status === 403) {
          // El cliente ya evita llegar hasta acá con el perfil incompleto,
          // pero por si esta pestaña quedó abierta desde antes de completarlo.
          window.location.href = window.PROFILE_URL;
          return;
        }

        const data = await response.json().catch(() => ({}));

        if (!response.ok) {
          showError(data.detail || 'No pudimos confirmar el turno. Probá de nuevo.');
          return;
        }

        // Éxito: la grilla se regenera en el servidor, así que recargamos
        sessionStorage.setItem('turnoOk', data.message || 'Turno confirmado.');
        window.location.reload();
      } catch (_) {
        showError('Problema de conexión. Revisá tu internet e intentá de nuevo.');
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Confirmar turno';
      }
    });
  }

  /* ---------- Aviso de éxito después de recargar ---------- */
  const okMessage = sessionStorage.getItem('turnoOk');
  if (okMessage) {
    sessionStorage.removeItem('turnoOk');

    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.setAttribute('role', 'status');
    toast.innerHTML =
      '<strong>✓ ' + okMessage + '</strong>' +
      '<a href="/mis-turnos">Ver mis turnos</a>';
    document.body.appendChild(toast);

    setTimeout(() => toast.classList.add('is-leaving'), 6000);
    setTimeout(() => toast.remove(), 6600);
  }
})();
