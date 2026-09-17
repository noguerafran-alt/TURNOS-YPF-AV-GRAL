/* Banner grado combustible — código de color internacional (AVGAS rojo / JET negro). */
'use strict';

(function (global) {
  const AVGAS_BG = '#C0392B';
  const JET_BG = '#1C1C1C';

  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  function cleanTipo(tipo, forCliente) {
    const t = String(tipo || '').trim();
    if (!t) return '';
    if (forCliente && /^(S\/D|SD|N\/D|ND|-|—)$/i.test(t)) return '';
    return t;
  }

  function resolveKind(combustible) {
    const raw = String(combustible || '').trim();
    if (!raw) return { kind: '', label: '', colorCode: '' };
    const u = raw.toUpperCase().replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim();
    const compact = u.replace(/\s+/g, '');
    if (compact.includes('JET') || u.includes('AEROKEROSENE')) {
      return { kind: 'jet', label: 'JET A-1', colorCode: 'NEGRO' };
    }
    if (u.includes('AVGAS') || compact.includes('100LL')) {
      return { kind: 'avgas', label: 'AVGAS 100LL', colorCode: 'ROJO' };
    }
    return { kind: 'unknown', label: raw, colorCode: '' };
  }

  /**
   * @param {object} opts
   * @param {string} opts.combustible
   * @param {string} [opts.matricula]
   * @param {string} [opts.tipo]
   * @param {string} [opts.cliente]
   * @param {'cliente'|'staff'} [opts.audience]
   * @param {'turno'|'maestro'} [opts.variant]
   * @param {boolean} [opts.lockedByMaestro]
   */
  function buildFuelBanner(opts) {
    const audience = opts.audience || 'cliente';
    const variant = opts.variant || 'turno';
    const locked = !!(opts.lockedByMaestro || variant === 'maestro');
    const { kind, label, colorCode } = resolveKind(opts.combustible);
    if (!kind) {
      return { show: false, kind: 'empty', label: '', colorCode: '', cssMod: '', bg: '', subline: '', html: '' };
    }

    let cssMod = 'fuel-banner--unknown';
    let bg = 'transparent';
    if (kind === 'avgas') { cssMod = 'fuel-banner--avgas'; bg = AVGAS_BG; }
    else if (kind === 'jet') { cssMod = 'fuel-banner--jet'; bg = JET_BG; }

    const mat = String(opts.matricula || '').trim();
    const tipo = cleanTipo(opts.tipo, audience === 'cliente');
    const cli = audience === 'staff' ? String(opts.cliente || '').trim() : '';
    const codeTxt = colorCode ? ('Código de color internacional: ' + colorCode) : '';

    let subline = '';
    if (locked && mat) {
      const tip = tipo ? (' (' + tipo + ')') : '';
      const head = audience === 'staff'
        ? ('Grado bloqueado por el maestro para ' + mat + tip)
        : ('Grado según matrícula ' + mat + tip);
      subline = codeTxt ? (head + ' · ' + codeTxt) : head;
    } else {
      const parts = [];
      if (mat) parts.push(mat);
      if (tipo) parts.push(tipo);
      if (cli) parts.push(cli);
      if (codeTxt) parts.push(codeTxt);
      subline = parts.join(' · ');
    }

    return {
      show: true,
      kind,
      label,
      colorCode,
      cssMod,
      bg,
      subline,
      html: '', // filled by renderFuelBanner
    };
  }

  function renderFuelBanner(opts) {
    const b = buildFuelBanner(opts || {});
    if (!b.show) return '';
    const sticky = opts && opts.sticky ? ' fuel-banner--sticky' : '';
    return (
      '<div class="fuel-banner ' + b.cssMod + sticky + '" role="status" style="background:' + b.bg + '">' +
        '<div class="fuel-banner__title">' +
          '<span class="fuel-banner__icon" aria-hidden="true">⛽</span>' +
          '<span class="fuel-banner__grade">' + escapeHtml(b.label) + '</span>' +
        '</div>' +
        (b.subline ? ('<p class="fuel-banner__sub">' + escapeHtml(b.subline) + '</p>') : '') +
      '</div>'
    );
  }

  /** Chip compacto para filas de tabla. */
  function fuelChipHtml(combustible) {
    const { kind, label } = resolveKind(combustible);
    if (!kind) return '';
    if (kind === 'jet') return '<span class="chip chip--jet">JET</span>';
    if (kind === 'avgas') return '<span class="chip chip--avgas">AVGAS</span>';
    return '<span class="chip">' + escapeHtml(label) + '</span>';
  }

  /** Chip con label completo (compat con paneles existentes). */
  function fuelChipFullHtml(combustible) {
    const { kind, label } = resolveKind(combustible);
    if (!kind) return '';
    if (kind === 'jet') return '<span class="chip chip--jet">JET A-1</span>';
    if (kind === 'avgas') return '<span class="chip chip--avgas">AVGAS 100LL</span>';
    return combustible ? ('<span class="chip">' + escapeHtml(combustible) + '</span>') : '';
  }

  global.FuelBanner = {
    build: buildFuelBanner,
    render: renderFuelBanner,
    chip: fuelChipHtml,
    chipFull: fuelChipFullHtml,
    resolve: resolveKind,
    escapeHtml,
  };
})(typeof window !== 'undefined' ? window : globalThis);
