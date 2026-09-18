/* Banner grado combustible — logos oficiales (AVGAS rojo / JET negro). */
'use strict';

(function (global) {
  const AVGAS_BG = '#C0392B';
  const JET_BG = '#1C1C1C';

  const LOGO = {
    jet: {
      svg: '/static/img/fuel/jet-a1.svg',
      png: '/static/img/fuel/jet-a1-oficial.png',
      alt: 'JET A-1',
    },
    avgas: {
      svg: '/static/img/fuel/avgas-100ll.svg',
      png: '/static/img/fuel/avgas-100ll-oficial.png',
      alt: 'AVGAS 100 LL',
    },
  };

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

  function logoMarkup(kind, { chip } = {}) {
    const L = LOGO[kind];
    if (!L) return '';
    const cls = chip ? 'chip__logo' : 'fuel-banner__logo';
    const wh = chip ? ' width="120" height="18"' : ' width="480" height="72"';
    return (
      '<picture' + (chip ? '' : ' class="fuel-banner__picture"') + '>' +
        '<source type="image/svg+xml" srcset="' + L.svg + '">' +
        '<img class="' + cls + '" src="' + L.png + '" alt="' + escapeHtml(L.alt) + '"' +
          wh + ' loading="lazy" decoding="async">' +
      '</picture>'
    );
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
      return {
        show: false, kind: 'empty', label: '', colorCode: '', cssMod: '', bg: '',
        subline: '', logoSvg: '', logoPng: '', logoAlt: '', html: '',
      };
    }

    let cssMod = 'fuel-banner--unknown';
    let bg = 'transparent';
    if (kind === 'avgas') { cssMod = 'fuel-banner--avgas'; bg = AVGAS_BG; }
    else if (kind === 'jet') { cssMod = 'fuel-banner--jet'; bg = JET_BG; }

    const logo = LOGO[kind] || null;
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
      logoSvg: logo ? logo.svg : '',
      logoPng: logo ? logo.png : '',
      logoAlt: logo ? logo.alt : label,
      html: '', // filled by renderFuelBanner
    };
  }

  function renderFuelBanner(opts) {
    const b = buildFuelBanner(opts || {});
    if (!b.show) return '';
    const sticky = opts && opts.sticky ? ' fuel-banner--sticky' : '';
    const logoClass = b.logoSvg ? ' fuel-banner--logo' : '';
    const styleBg = b.logoSvg ? '' : (' style="background:' + b.bg + '"');

    let body;
    if (b.logoSvg) {
      body = logoMarkup(b.kind);
    } else {
      body = (
        '<div class="fuel-banner__title">' +
          '<span class="fuel-banner__icon" aria-hidden="true">⛽</span>' +
          '<span class="fuel-banner__grade">' + escapeHtml(b.label) + '</span>' +
        '</div>'
      );
    }

    return (
      '<div class="fuel-banner ' + b.cssMod + logoClass + sticky + '" role="status"' + styleBg + '>' +
        body +
        (b.subline ? ('<p class="fuel-banner__sub">' + escapeHtml(b.subline) + '</p>') : '') +
      '</div>'
    );
  }

  /** Chip compacto para filas de tabla — logo pequeño si hay. */
  function fuelChipHtml(combustible) {
    const { kind, label } = resolveKind(combustible);
    if (!kind) return '';
    if (kind === 'jet' || kind === 'avgas') {
      const chipClass = kind === 'jet' ? 'chip--jet' : 'chip--avgas';
      const alt = LOGO[kind].alt;
      return (
        '<span class="chip chip--logo ' + chipClass + '" title="' + escapeHtml(alt) + '">' +
          logoMarkup(kind, { chip: true }) +
        '</span>'
      );
    }
    return '<span class="chip">' + escapeHtml(label) + '</span>';
  }

  /** Chip con label completo (compat con paneles existentes). */
  function fuelChipFullHtml(combustible) {
    const { kind, label } = resolveKind(combustible);
    if (!kind) return '';
    if (kind === 'jet' || kind === 'avgas') {
      return fuelChipHtml(combustible);
    }
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
