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
      bookDialog.showModal();
    });
  });

  /* ---------- Prefill desde Mis Aeronaves ---------- */
  const aircraftPick = document.getElementById('aircraft_pick');
  const fuelHint = document.getElementById('aircraftFuelHint');

  function resetAircraftPick() {
    if (aircraftPick) aircraftPick.value = '';
    if (fuelHint) {
      fuelHint.hidden = true;
      fuelHint.textContent = '';
    }
  }

  function applyAircraftPick() {
    if (!aircraftPick || !bookForm) return;
    const opt = aircraftPick.selectedOptions[0];
    if (!opt || !opt.value) {
      if (fuelHint) {
        fuelHint.hidden = true;
        fuelHint.textContent = '';
      }
      return;
    }
    const matricula = opt.dataset.matricula || '';
    const modelo = opt.dataset.modelo || '';
    const combustible = opt.dataset.combustible || '';
    if (matricula) bookForm.aircraft.value = matricula;
    if (modelo) bookForm.aircraft_model.value = modelo;
    if (fuelHint) {
      if (combustible) {
        fuelHint.textContent = 'Combustible de tu lista: ' + combustible + ' (se confirma con el listado al reservar).';
        fuelHint.hidden = false;
      } else {
        fuelHint.textContent = 'Sin combustible en tu lista: si es primera carga, el operador lo reconfirmará.';
        fuelHint.hidden = false;
      }
    }
  }

  if (aircraftPick) {
    aircraftPick.addEventListener('change', applyAircraftPick);
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
      const payload = {
        slug: window.AGENDA_SLUG,
        starts_at: selectedSlot,
        aircraft: bookForm.aircraft.value.trim(),
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
