/**
 * A2UI Renderer — Vanilla JS renderer for declarative A2UI JSON component trees.
 * Maps agent-emitted JSON documents to DOM elements for the Lumen learning platform.
 *
 * Supported component types:
 *   Layout   — Card, Row, Grid, List, Divider
 *   Text     — Heading, Text, Quote, CodeBlock
 *   Data     — KeyValue, Stat, ProgressBar, Badge, Alert, Rating
 *   Charts   — BarChart, PieChart, Gauge, LineChart, Sparkline (CSS/SVG, no canvas)
 *   Rich     — Table, Stepper, Checklist, Tabs, Accordion
 *   Interactive — Button, Form, Input, Select, Checkbox, Toggle, Slider, Chip, ChatInput, ThemeToggle
 *   Overlay  — Modal, Drawer
 *   Media    — Image, Avatar
 *   Nav      — Breadcrumb, EmptyState
 *   Calendar — Calendar (month grid with event dots)
 *
 * Theme: warm beige (#faf8f4) by default; dark mode via data-theme="dark".
 * All CSS classes are prefixed with `a2ui-` to avoid conflicts.
 * No external dependencies — works in legacy HTML pages and modern SPAs.
 *
 * @example
 *   // Mount into a container
 *   window.renderA2UI(containerEl, a2uiDoc, (action) => console.log(action));
 *
 *   // Incremental patching
 *   window.patchA2UI(containerEl, [{ op: "replace", targetId: "p1", component: {...} }]);
 *
 * @version 1.0.0
 * @license MIT
 */
(function () {
  "use strict";

  /* ───────────────────────────── Theme tokens ───────────────────────────── */

  /** Light-mode semantic colours keyed by tone name */
  var TONES = {
    success: "#2e7d32",
    error: "#c62828",
    warning: "#ef6c00",
    muted: "#8a8478",
    info: "#1565c0",
    accent: "#d4a853",
  };

  /** Dark-mode equivalents (higher contrast for accessibility) */
  var DARK_TONES = {
    success: "#66bb6a",
    error: "#ef5350",
    warning: "#ffa726",
    muted: "#9e9e9e",
    info: "#42a5f5",
    accent: "#f0c97b",
  };

  /* ──────────────────────── CSS injection (once) ────────────────────────── */

  let stylesInjected = false;

  function injectStyles() {
    if (stylesInjected) return;
    stylesInjected = true;

    const css = `
/* ── A2UI base reset ── */
.a2ui-root { font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; color: #2c2820; line-height: 1.5; box-sizing: border-box; }
.a2ui-root *, .a2ui-root *::before, .a2ui-root *::after { box-sizing: inherit; }

/* ── Light theme ── */
.a2ui-root { --bg: #faf8f4; --bg-card: #ffffff; --text: #2c2820; --text-muted: #8a8478; --border: #e0dcd4; --accent: #d4a853; --accent-light: #f5ecd7; --shadow: 0 2px 8px rgba(44,40,32,.10); --radius: 8px; }

/* ── Dark theme ── */
.a2ui-root[data-theme="dark"] { --bg: #1e1e1e; --bg-card: #2a2a2a; --text: #e0dcd4; --text-muted: #9e9e9e; --border: #444; --accent: #f0c97b; --accent-light: #3a3224; --shadow: 0 2px 8px rgba(0,0,0,.35); }
.a2ui-root[data-theme="dark"] { color: var(--text); }

/* ── Card ── */
.a2ui-card { background: var(--bg-card); border-radius: var(--radius); padding: 16px; margin-bottom: 12px; }
.a2ui-card--elevated { box-shadow: var(--shadow); }
.a2ui-card--outlined { border: 1px solid var(--border); }

/* ── Row ── */
.a2ui-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; }

/* ── Grid ── */
.a2ui-grid { display: grid; margin-bottom: 12px; }

/* ── List ── */
.a2ui-list { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; }
.a2ui-list__title { font-weight: 600; margin-bottom: 4px; color: var(--text); }

/* ── Divider ── */
.a2ui-divider { border: none; border-top: 1px solid var(--border); margin: 12px 0; }

/* ── Heading ── */
.a2ui-heading { margin: 0 0 8px; color: var(--text); font-weight: 600; }

/* ── Text ── */
.a2ui-text { margin: 0 0 8px; }

/* ── Quote ── */
.a2ui-quote { border-left: 4px solid var(--accent); padding: 8px 16px; margin: 0 0 12px; background: var(--accent-light); border-radius: 0 var(--radius) var(--radius) 0; }
.a2ui-quote__author { font-style: italic; color: var(--text-muted); margin-top: 6px; font-size: .9em; }

/* ── CodeBlock ── */
.a2ui-codeblock { background: #1e1e2e; color: #cdd6f4; padding: 14px; border-radius: var(--radius); overflow-x: auto; font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: .88em; line-height: 1.6; margin-bottom: 12px; white-space: pre; }
.a2ui-syn-kw { color: #cba6f7; }
.a2ui-syn-str { color: #a6e3a1; }
.a2ui-syn-num { color: #fab387; }
.a2ui-syn-cm { color: #6c7086; font-style: italic; }
.a2ui-syn-fn { color: #89b4fa; }
.a2ui-syn-op { color: #89dceb; }

/* ── KeyValue ── */
.a2ui-kv { display: flex; justify-content: space-between; padding: 4px 0; }
.a2ui-kv__label { color: var(--text-muted); }
.a2ui-kv__value { font-weight: 600; }

/* ── Stat ── */
.a2ui-stat { text-align: center; padding: 12px; }
.a2ui-stat__value { font-size: 2em; font-weight: 700; color: var(--accent); }
.a2ui-stat__label { color: var(--text-muted); font-size: .9em; }
.a2ui-stat__trend { font-size: .85em; margin-left: 6px; }

/* ── ProgressBar ── */
.a2ui-progress { margin-bottom: 10px; }
.a2ui-progress__track { height: 10px; background: var(--border); border-radius: 5px; overflow: hidden; }
.a2ui-progress__fill { height: 100%; border-radius: 5px; transition: width .4s ease; }
.a2ui-progress__head { display: flex; justify-content: space-between; margin-bottom: 4px; font-size: .9em; }

/* ── Badge ── */
.a2ui-badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: .82em; font-weight: 600; }

/* ── Alert ── */
.a2ui-alert { padding: 12px 16px; border-radius: var(--radius); margin-bottom: 12px; border-left: 4px solid; }
.a2ui-alert__title { font-weight: 600; margin-bottom: 2px; }

/* ── Rating ── */
.a2ui-rating { font-size: 1.3em; letter-spacing: 2px; margin-bottom: 8px; }

/* ── BarChart ── */
.a2ui-barchart { margin-bottom: 14px; }
.a2ui-barchart__title { font-weight: 600; margin-bottom: 8px; }
.a2ui-barchart__bars { display: flex; align-items: flex-end; gap: 8px; height: 140px; }
.a2ui-barchart__col { display: flex; flex-direction: column; align-items: center; flex: 1; height: 100%; justify-content: flex-end; }
.a2ui-barchart__bar { width: 100%; min-width: 20px; border-radius: 4px 4px 0 0; transition: height .4s ease; }
.a2ui-barchart__lbl { font-size: .75em; margin-top: 4px; color: var(--text-muted); text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 60px; }
.a2ui-barchart__val { font-size: .75em; margin-bottom: 2px; font-weight: 600; }

/* ── PieChart ── */
.a2ui-piechart { margin-bottom: 14px; }
.a2ui-piechart__title { font-weight: 600; margin-bottom: 8px; }
.a2ui-piechart__wrap { display: flex; align-items: center; gap: 20px; }
.a2ui-piechart__circle { width: 120px; height: 120px; border-radius: 50%; flex-shrink: 0; }
.a2ui-piechart__legend { display: flex; flex-direction: column; gap: 4px; font-size: .85em; }
.a2ui-piechart__swatch { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 6px; vertical-align: middle; }

/* ── Gauge ── */
.a2ui-gauge { text-align: center; margin-bottom: 14px; }
.a2ui-gauge__label { font-size: .9em; color: var(--text-muted); margin-top: 4px; }

/* ── Table ── */
.a2ui-table { width: 100%; border-collapse: collapse; margin-bottom: 14px; font-size: .92em; }
.a2ui-table th, .a2ui-table td { padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border); }
.a2ui-table th { font-weight: 600; background: var(--accent-light); }

/* ── Stepper ── */
.a2ui-stepper { display: flex; align-items: center; gap: 0; margin-bottom: 14px; overflow-x: auto; }
.a2ui-stepper__step { display: flex; align-items: center; gap: 6px; white-space: nowrap; font-size: .88em; }
.a2ui-stepper__dot { width: 22px; height: 22px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: .75em; font-weight: 700; color: #fff; flex-shrink: 0; }
.a2ui-stepper__line { width: 28px; height: 2px; background: var(--border); flex-shrink: 0; }

/* ── Checklist ── */
.a2ui-checklist { margin-bottom: 12px; }
.a2ui-checklist__title { font-weight: 600; margin-bottom: 6px; }
.a2ui-checklist__item { display: flex; align-items: center; gap: 8px; padding: 3px 0; }
.a2ui-checklist__box { width: 18px; height: 18px; border: 2px solid var(--border); border-radius: 4px; display: flex; align-items: center; justify-content: center; font-size: .7em; flex-shrink: 0; }
.a2ui-checklist__box--done { background: var(--accent); border-color: var(--accent); color: #fff; }

/* ── Button ── */
.a2ui-btn { background: var(--accent); color: #fff; border: none; padding: 8px 20px; border-radius: var(--radius); font-size: .92em; font-weight: 600; cursor: pointer; transition: opacity .2s; }
.a2ui-btn:hover { opacity: .85; }

/* ── Form ── */
.a2ui-form { margin-bottom: 14px; }
.a2ui-form__title { font-weight: 600; margin-bottom: 10px; }

/* ── Input ── */
.a2ui-input { margin-bottom: 10px; display: flex; flex-direction: column; gap: 4px; }
.a2ui-input__label { font-size: .88em; font-weight: 500; }
.a2ui-input__field { padding: 8px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: .92em; background: var(--bg); color: var(--text); }

/* ── Select ── */
.a2ui-select { margin-bottom: 10px; display: flex; flex-direction: column; gap: 4px; }
.a2ui-select__label { font-size: .88em; font-weight: 500; }
.a2ui-select__field { padding: 8px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: .92em; background: var(--bg); color: var(--text); }

/* ── Checkbox ── */
.a2ui-checkbox { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; cursor: pointer; }
.a2ui-checkbox__box { width: 18px; height: 18px; border: 2px solid var(--border); border-radius: 4px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; transition: background .15s; }
.a2ui-checkbox__box--on { background: var(--accent); border-color: var(--accent); color: #fff; }

/* ── Calendar ── */
.a2ui-calendar { margin-bottom: 14px; }
.a2ui-calendar__title { font-weight: 600; text-align: center; margin-bottom: 8px; }
.a2ui-calendar__grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; font-size: .82em; text-align: center; }
.a2ui-calendar__dow { font-weight: 600; padding: 4px; color: var(--text-muted); }
.a2ui-calendar__day { padding: 4px; border-radius: 4px; min-height: 28px; position: relative; }
.a2ui-calendar__day--today { outline: 2px solid var(--accent); }
.a2ui-calendar__event { width: 6px; height: 6px; border-radius: 50%; display: inline-block; margin: 1px; }

/* ── Modal ── */
.a2ui-modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.45); display: flex; align-items: center; justify-content: center; z-index: 9000; }
.a2ui-modal { background: var(--bg-card); border-radius: var(--radius); box-shadow: var(--shadow); padding: 20px; min-width: 320px; max-width: 90vw; max-height: 85vh; overflow-y: auto; position: relative; }
.a2ui-modal__header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.a2ui-modal__title { font-weight: 600; font-size: 1.1em; }
.a2ui-modal__close { background: none; border: none; font-size: 1.3em; cursor: pointer; color: var(--text-muted); padding: 0 4px; }
.a2ui-modal__close:hover { color: var(--text); }

/* ── Drawer ── */
.a2ui-drawer-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.35); z-index: 8999; }
.a2ui-drawer { position: fixed; top: 0; bottom: 0; width: 340px; max-width: 85vw; background: var(--bg-card); box-shadow: var(--shadow); z-index: 9000; display: flex; flex-direction: column; overflow-y: auto; }
.a2ui-drawer--right { right: 0; }
.a2ui-drawer--left { left: 0; }
.a2ui-drawer__header { display: flex; justify-content: space-between; align-items: center; padding: 16px; border-bottom: 1px solid var(--border); }
.a2ui-drawer__title { font-weight: 600; font-size: 1.05em; }
.a2ui-drawer__close { background: none; border: none; font-size: 1.3em; cursor: pointer; color: var(--text-muted); padding: 0 4px; }
.a2ui-drawer__close:hover { color: var(--text); }
.a2ui-drawer__body { padding: 16px; flex: 1; }

/* ── LineChart ── */
.a2ui-linechart { margin-bottom: 14px; }
.a2ui-linechart__title { font-weight: 600; margin-bottom: 8px; }
.a2ui-linechart__svg { width: 100%; overflow: visible; }
.a2ui-linechart__labels { display: flex; justify-content: space-between; font-size: .75em; color: var(--text-muted); margin-top: 4px; }

/* ── Sparkline ── */
.a2ui-sparkline { display: inline-flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.a2ui-sparkline__label { font-size: .85em; color: var(--text-muted); }

/* ── Image ── */
.a2ui-image { margin-bottom: 12px; }
.a2ui-image__img { max-width: 100%; border-radius: var(--radius); display: block; }
.a2ui-image__caption { font-size: .85em; color: var(--text-muted); margin-top: 4px; }

/* ── Avatar ── */
.a2ui-avatar { width: 40px; height: 40px; border-radius: 50%; overflow: hidden; display: inline-flex; align-items: center; justify-content: center; background: var(--accent-light); color: var(--accent); font-weight: 700; font-size: .9em; flex-shrink: 0; }
.a2ui-avatar__img { width: 100%; height: 100%; object-fit: cover; }

/* ── Toggle ── */
.a2ui-toggle { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; cursor: pointer; }
.a2ui-toggle__track { width: 40px; height: 22px; border-radius: 11px; background: var(--border); position: relative; transition: background .2s; flex-shrink: 0; }
.a2ui-toggle__track--on { background: var(--accent); }
.a2ui-toggle__thumb { width: 18px; height: 18px; border-radius: 50%; background: #fff; position: absolute; top: 2px; left: 2px; transition: left .2s; }
.a2ui-toggle__track--on .a2ui-toggle__thumb { left: 20px; }
.a2ui-toggle__label { font-size: .92em; }

/* ── Slider ── */
.a2ui-slider { margin-bottom: 10px; display: flex; flex-direction: column; gap: 4px; }
.a2ui-slider__label { font-size: .88em; font-weight: 500; }
.a2ui-slider__field { -webkit-appearance: none; appearance: none; width: 100%; height: 6px; border-radius: 3px; background: var(--border); outline: none; }
.a2ui-slider__field::-webkit-slider-thumb { -webkit-appearance: none; appearance: none; width: 18px; height: 18px; border-radius: 50%; background: var(--accent); cursor: pointer; }

/* ── Chip ── */
.a2ui-chip { display: inline-block; padding: 4px 14px; border-radius: 999px; font-size: .85em; font-weight: 500; background: var(--accent-light); color: var(--accent); cursor: pointer; margin: 2px 4px 2px 0; border: 1px solid var(--accent); transition: background .15s; }
.a2ui-chip:hover { background: var(--accent); color: #fff; }

/* ── ChatInput ── */
.a2ui-chatinput { display: flex; gap: 8px; margin-bottom: 12px; }
.a2ui-chatinput__field { flex: 1; padding: 8px 12px; border: 1px solid var(--border); border-radius: var(--radius); font-size: .92em; background: var(--bg); color: var(--text); }
.a2ui-chatinput__btn { background: var(--accent); color: #fff; border: none; padding: 8px 16px; border-radius: var(--radius); font-size: .92em; font-weight: 600; cursor: pointer; transition: opacity .2s; }
.a2ui-chatinput__btn:hover { opacity: .85; }

/* ── ThemeToggle ── */
.a2ui-themetoggle { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; margin-bottom: 8px; font-size: .92em; }
.a2ui-themetoggle__icon { font-size: 1.2em; }

/* ── Breadcrumb ── */
.a2ui-breadcrumb { display: flex; align-items: center; gap: 4px; font-size: .88em; margin-bottom: 10px; flex-wrap: wrap; }
.a2ui-breadcrumb__item { color: var(--text-muted); }
.a2ui-breadcrumb__item--link { color: var(--accent); cursor: pointer; text-decoration: none; }
.a2ui-breadcrumb__item--link:hover { text-decoration: underline; }
.a2ui-breadcrumb__sep { color: var(--text-muted); margin: 0 2px; }

/* ── EmptyState ── */
.a2ui-emptystate { text-align: center; padding: 32px 16px; margin-bottom: 14px; }
.a2ui-emptystate__icon { font-size: 2.5em; margin-bottom: 8px; }
.a2ui-emptystate__title { font-weight: 600; font-size: 1.1em; margin-bottom: 4px; }
.a2ui-emptystate__msg { color: var(--text-muted); margin-bottom: 12px; font-size: .92em; }

/* ── Tabs ── */
.a2ui-tabs { margin-bottom: 14px; }
.a2ui-tabs__nav { display: flex; border-bottom: 2px solid var(--border); margin-bottom: 12px; gap: 0; }
.a2ui-tabs__tab { padding: 8px 16px; cursor: pointer; font-size: .92em; font-weight: 500; color: var(--text-muted); border-bottom: 2px solid transparent; margin-bottom: -2px; transition: color .15s, border-color .15s; background: none; border-top: none; border-left: none; border-right: none; }
.a2ui-tabs__tab--active { color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; }
.a2ui-tabs__panel { display: none; }
.a2ui-tabs__panel--active { display: block; }

/* ── Accordion ── */
.a2ui-accordion { margin-bottom: 14px; }
.a2ui-accordion__item { border: 1px solid var(--border); border-radius: var(--radius); margin-bottom: 6px; overflow: hidden; }
.a2ui-accordion__header { display: flex; justify-content: space-between; align-items: center; padding: 10px 14px; cursor: pointer; font-weight: 500; background: var(--accent-light); transition: background .15s; }
.a2ui-accordion__header:hover { background: var(--border); }
.a2ui-accordion__arrow { transition: transform .2s; font-size: .8em; }
.a2ui-accordion__arrow--open { transform: rotate(90deg); }
.a2ui-accordion__body { padding: 0 14px; max-height: 0; overflow: hidden; transition: max-height .25s ease, padding .25s ease; }
.a2ui-accordion__body--open { padding: 10px 14px; max-height: 600px; }
`;
    const style = document.createElement("style");
    style.textContent = css;
    style.id = "a2ui-styles";
    document.head.appendChild(style);
  }

  /* ──────────────────────── Utility helpers ──────────────────────────── */

  /**
   * Create a DOM element with optional class and attributes.
   * @param {string} tag   - HTML tag name
   * @param {string} [cls] - CSS class string
   * @param {Object<string,string>} [attrs] - HTML attributes
   * @returns {HTMLElement}
   */
  function el(tag, cls, attrs) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (attrs) Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }

  /** Append a text node to parent and return parent for chaining. */
  function txt(parent, t) { parent.appendChild(document.createTextNode(t)); return parent; }

  /**
   * Resolve tone colour for current theme.
   * @param {string} tone - One of success/error/warning/muted/info/accent
   * @param {boolean} dark - Whether dark mode is active
   * @returns {string} CSS colour hex string
   */
  function toneColor(tone, dark) {
    return (dark ? DARK_TONES : TONES)[tone] || (dark ? DARK_TONES : TONES).accent;
  }

  /** Detect whether the container (or its a2ui-root ancestor) is in dark mode. */
  function isDark(container) {
    var root = container.closest(".a2ui-root") || container;
    return root.getAttribute("data-theme") === "dark";
  }

  /** Clamp a number between min and max (inclusive). */
  function clamp(val, min, max) { return Math.max(min, Math.min(max, val)); }

  /** Safely coerce to number, with fallback. */
  function num(v, fallback) { var n = Number(v); return isNaN(n) ? (fallback || 0) : n; }

  /** Palette for chart segments (8 distinguishable colours). */
  var CHART_PALETTE = ["#d4a853", "#5b8c5a", "#c0392b", "#2980b9", "#8e44ad", "#e67e22", "#1abc9c", "#e84393"];

  /* ──────────────────── Syntax highlighting (Python / JS) ─────────────── */

  /**
   * Token rules applied in order. Each rule wraps matches in a coloured span.
   * Rules are intentionally broad — this is a display highlighter, not a parser.
   */
  var SYN_RULES = [
    { cls: "a2ui-syn-cm", re: /(\/\/.*$|\/\*[\s\S]*?\*\/|#.*$)/gm },
    { cls: "a2ui-syn-str", re: /("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)/g },
    { cls: "a2ui-syn-kw", re: /\b(function|return|const|let|var|if|else|for|while|class|import|export|from|def|print|True|False|None|async|await|try|catch|finally|throw|new|this|self|in|of|yield|lambda|with|as|raise|except|pass|break|continue)\b/g },
    { cls: "a2ui-syn-fn", re: /\b([a-zA-Z_]\w*)\s*(?=\()/g },
    { cls: "a2ui-syn-num", re: /\b(\d+\.?\d*)\b/g },
    { cls: "a2ui-syn-op", re: /([+\-*/%=<>!&|^~?:]+)/g },
  ];

  /**
   * Apply syntax highlighting to a code string.
   * Returns HTML with spans wrapping keywords, strings, numbers, etc.
   * @param {string} code - Raw source code
   * @returns {string} HTML string with syntax-coloured spans
   */
  function highlight(code) {
    var text = code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    // Apply rules in order, protecting already-wrapped spans
    SYN_RULES.forEach(function (rule) {
      text = text.replace(rule.re, function (m, g1) {
        if (m.indexOf('class="a2ui-syn') !== -1) return m;
        return '<span class="' + rule.cls + '">' + (g1 || m) + "</span>";
      });
    });
    return text;
  }

  /* ──────────────── Component factory map ────────────────────────────── */

  /** @type {Object<string, function(Object, HTMLElement[], function, boolean): HTMLElement>} */
  var factories = {};

  /* ── Layout ── */

  /** Card — container with shadow or border. @param {{variant: "elevated"|"outlined"}} props */
  factories.Card = function (props, children) {
    var v = (props.variant === "outlined") ? "outlined" : "elevated";
    var c = el("div", "a2ui-card a2ui-card--" + v, { role: "region" });
    children.forEach(function (ch) { c.appendChild(ch); });
    return c;
  };

  /** Row — horizontal flex container. Optional label/value pair display. */
  /** Row — horizontal flex container. Optional label/value pair display. */
  factories.Row = function (props, children) {
    var r = el("div", "a2ui-row");
    if (props.label) { var l = el("span", "a2ui-kv__label"); txt(l, props.label); r.appendChild(l); }
    if (props.value != null) { var v = el("span", "a2ui-kv__value"); txt(v, String(props.value)); r.appendChild(v); }
    children.forEach(function (ch) { r.appendChild(ch); });
    return r;
  };

  /** Grid — CSS grid layout. `columns` can be a number or CSS grid-template-columns string. */
  /** Grid — CSS grid layout. `columns` can be a number or CSS grid-template-columns string. */
  factories.Grid = function (props, children) {
    var g = el("div", "a2ui-grid");
    var cols = props.columns || 2;
    g.style.gridTemplateColumns = typeof cols === "number" ? "repeat(" + cols + ", 1fr)" : cols;
    g.style.gap = (props.gap != null ? props.gap : 12) + "px";
    children.forEach(function (ch) { g.appendChild(ch); });
    return g;
  };

  /** List — vertical stack of children with optional title. */
  factories.List = function (props, children) {
    var w = el("div", "a2ui-list", { role: "list" });
    if (props.title) { var t = el("div", "a2ui-list__title"); txt(t, props.title); w.appendChild(t); }
    children.forEach(function (ch) {
      ch.setAttribute("role", "listitem");
      w.appendChild(ch);
    });
    return w;
  };

  /** Divider — thin horizontal rule. */
  factories.Divider = function () {
    return el("hr", "a2ui-divider", { role: "separator" });
  };

  /* ── Text ── */

  /** Heading — renders h1–h6 based on `level` prop. */
  factories.Heading = function (props) {
    var level = clamp(num(props.level, 2), 1, 6);
    var h = el("h" + level, "a2ui-heading");
    txt(h, props.text || "");
    return h;
  };

  /** Text — paragraph with optional tone colouring (muted/success/error/warning). */
  factories.Text = function (props, _c, _a, dark) {
    var p = el("p", "a2ui-text");
    txt(p, props.text || "");
    if (props.tone) p.style.color = toneColor(props.tone, dark);
    return p;
  };

  /** Quote — blockquote with optional author and source attribution. */
  factories.Quote = function (props) {
    var q = el("blockquote", "a2ui-quote");
    var body = el("div"); txt(body, props.text || ""); q.appendChild(body);
    if (props.author || props.source) {
      var a = el("div", "a2ui-quote__author");
      var cite = "— " + (props.author || "");
      if (props.source) cite += ", " + props.source;
      txt(a, cite);
      q.appendChild(a);
    }
    return q;
  };

  /**
   * CodeBlock — preformatted code with basic syntax highlighting.
   * Supports Python and JavaScript keywords, strings, numbers, comments.
   */
  factories.CodeBlock = function (props) {
    var pre = el("pre", "a2ui-codeblock");
    if (props.language) pre.setAttribute("data-lang", props.language);
    pre.innerHTML = highlight(props.code || "");
    return pre;
  };

  /* ── Data Display ── */

  /** KeyValue — horizontal label:value pair, optionally toned. */
  factories.KeyValue = function (props, _c, _a, dark) {
    var r = el("div", "a2ui-kv");
    var l = el("span", "a2ui-kv__label"); txt(l, props.label || "");
    var v = el("span", "a2ui-kv__value"); txt(v, String(props.value != null ? props.value : ""));
    if (props.tone) v.style.color = toneColor(props.tone, dark);
    r.appendChild(l); r.appendChild(v);
    return r;
  };

  /** Stat — large number display with label and optional trend arrow. */
  factories.Stat = function (props, _c, _a, dark) {
    var w = el("div", "a2ui-stat");
    var valRow = el("div");
    var v = el("span", "a2ui-stat__value"); txt(v, String(props.value != null ? props.value : "0"));
    valRow.appendChild(v);
    if (props.trend) {
      var tr = el("span", "a2ui-stat__trend");
      var isUp = props.trend === "up";
      tr.style.color = toneColor(isUp ? "success" : "error", dark);
      txt(tr, isUp ? "▲" : "▼");
      valRow.appendChild(tr);
    }
    w.appendChild(valRow);
    if (props.label) { var l = el("div", "a2ui-stat__label"); txt(l, props.label); w.appendChild(l); }
    return w;
  };

  /** ProgressBar — horizontal bar with label and percentage (0–100). */
  factories.ProgressBar = function (props, _c, _a, dark) {
    var pct = clamp(num(props.value, 0), 0, 100);
    var w = el("div", "a2ui-progress", { role: "progressbar", "aria-valuenow": String(pct), "aria-valuemin": "0", "aria-valuemax": "100" });
    var head = el("div", "a2ui-progress__head");
    var hl = el("span"); txt(hl, props.label || "");
    var hv = el("span"); txt(hv, pct + "%");
    head.appendChild(hl); head.appendChild(hv); w.appendChild(head);
    var track = el("div", "a2ui-progress__track");
    var fill = el("div", "a2ui-progress__fill");
    fill.style.width = pct + "%";
    fill.style.background = toneColor(props.tone || "accent", dark);
    track.appendChild(fill); w.appendChild(track);
    return w;
  };

  /** Badge — coloured pill with text and tone. */
  factories.Badge = function (props, _c, _a, dark) {
    var b = el("span", "a2ui-badge");
    var c = toneColor(props.tone || "accent", dark);
    b.style.background = c + "22";
    b.style.color = c;
    txt(b, props.text || "");
    return b;
  };

  /** Alert — banner with title, message, and coloured left border. */
  factories.Alert = function (props, _c, _a, dark) {
    var c = toneColor(props.tone || "info", dark);
    var a = el("div", "a2ui-alert", { role: "alert" });
    a.style.borderLeftColor = c;
    a.style.background = c + "12";
    if (props.title) { var t = el("div", "a2ui-alert__title"); t.style.color = c; txt(t, props.title); a.appendChild(t); }
    if (props.message) { var m = el("div"); txt(m, props.message); a.appendChild(m); }
    return a;
  };

  /** Rating — star display (0–5), supports half stars. */
  factories.Rating = function (props) {
    var val = clamp(num(props.value, 0), 0, 5);
    var w = el("div", "a2ui-rating", { role: "img", "aria-label": val + " out of 5 stars" });
    var full = Math.floor(val);
    var half = val % 1 >= 0.5 ? 1 : 0;
    var empty = 5 - full - half;
    var stars = "★".repeat(full) + (half ? "⯨" : "") + "☆".repeat(empty);
    w.innerHTML = '<span style="color:var(--accent)">' + "★".repeat(full) + "</span>" +
                  (half ? '<span style="color:var(--accent)">⯨</span>' : "") +
                  '<span style="color:var(--border)">' + "☆".repeat(empty) + "</span>";
    return w;
  };

  /* ── Charts (CSS-only, no canvas) ── */

  /**
   * BarChart — vertical bars using CSS flex.
   * @param {{title: string, data: Array<{label: string, value: number, tone?: string}>}} props
   */
  factories.BarChart = function (props, _c, _a, dark) {
    var data = props.data || [];
    var maxVal = Math.max.apply(null, data.map(function (d) { return d.value; }).concat([1]));
    var w = el("div", "a2ui-barchart");
    if (props.title) { var t = el("div", "a2ui-barchart__title"); txt(t, props.title); w.appendChild(t); }
    var bars = el("div", "a2ui-barchart__bars");
    data.forEach(function (d, i) {
      var col = el("div", "a2ui-barchart__col");
      var valEl = el("div", "a2ui-barchart__val"); txt(valEl, String(d.value));
      var bar = el("div", "a2ui-barchart__bar");
      bar.style.height = ((d.value / maxVal) * 100) + "%";
      bar.style.background = d.tone ? toneColor(d.tone, dark) : CHART_PALETTE[i % CHART_PALETTE.length];
      var lbl = el("div", "a2ui-barchart__lbl"); txt(lbl, d.label || "");
      col.appendChild(valEl); col.appendChild(bar); col.appendChild(lbl);
      bars.appendChild(col);
    });
    w.appendChild(bars);
    return w;
  };

  /**
   * PieChart — CSS conic-gradient with legend.
   * @param {{title: string, data: Array<{label: string, value: number, tone?: string}>}} props
   */
  factories.PieChart = function (props, _c, _a, dark) {
    var data = props.data || [];
    var total = data.reduce(function (s, d) { return s + d.value; }, 0) || 1;
    var w = el("div", "a2ui-piechart");
    if (props.title) { var t = el("div", "a2ui-piechart__title"); txt(t, props.title); w.appendChild(t); }
    var wrap = el("div", "a2ui-piechart__wrap");
    // Build conic-gradient
    var gradParts = []; var angle = 0;
    data.forEach(function (d, i) {
      var c = d.tone ? toneColor(d.tone, dark) : CHART_PALETTE[i % CHART_PALETTE.length];
      var slice = (d.value / total) * 360;
      gradParts.push(c + " " + angle + "deg " + (angle + slice) + "deg");
      angle += slice;
    });
    var circle = el("div", "a2ui-piechart__circle");
    circle.style.background = "conic-gradient(" + gradParts.join(", ") + ")";
    wrap.appendChild(circle);
    // Legend
    var legend = el("div", "a2ui-piechart__legend");
    data.forEach(function (d, i) {
      var row = el("div");
      var sw = el("span", "a2ui-piechart__swatch");
      sw.style.background = d.tone ? toneColor(d.tone, dark) : CHART_PALETTE[i % CHART_PALETTE.length];
      row.appendChild(sw);
      txt(row, d.label + " (" + Math.round((d.value / total) * 100) + "%)");
      legend.appendChild(row);
    });
    wrap.appendChild(legend);
    w.appendChild(wrap);
    return w;
  };

  /**
   * Gauge — circular SVG arc gauge with value, max, label, unit.
   * This is the only component that uses SVG (allowed per spec).
   */
  factories.Gauge = function (props) {
    var val = num(props.value, 0);
    var max = num(props.max, 100) || 100;
    var pct = clamp(val / max, 0, 1);
    var w = el("div", "a2ui-gauge");
    var size = 100; var stroke = 10; var r = (size - stroke) / 2;
    var circ = 2 * Math.PI * r;
    var dashFull = circ * 0.75; // 270 deg arc
    var dashVal = dashFull * pct;
    var svg = '<svg width="' + size + '" height="' + size + '" viewBox="0 0 ' + size + " " + size + '">'
      + '<circle cx="' + size / 2 + '" cy="' + size / 2 + '" r="' + r + '" fill="none" stroke="var(--border)" stroke-width="' + stroke + '" '
      + 'stroke-dasharray="' + dashFull + " " + circ + '" stroke-dashoffset="0" stroke-linecap="round" '
      + 'transform="rotate(135 ' + size / 2 + " " + size / 2 + ')"/>'
      + '<circle cx="' + size / 2 + '" cy="' + size / 2 + '" r="' + r + '" fill="none" stroke="var(--accent)" stroke-width="' + stroke + '" '
      + 'stroke-dasharray="' + dashVal + " " + circ + '" stroke-dashoffset="0" stroke-linecap="round" '
      + 'transform="rotate(135 ' + size / 2 + " " + size / 2 + ')"/>'
      + '<text x="' + size / 2 + '" y="' + (size / 2 + 6) + '" text-anchor="middle" fill="var(--text)" font-size="18" font-weight="700">'
      + val + (props.unit || "") + "</text></svg>";
    w.innerHTML = svg;
    if (props.label) { var l = el("div", "a2ui-gauge__label"); txt(l, props.label); w.appendChild(l); }
    return w;
  };

  /* ── Rich Data ── */

  /**
   * Table — HTML table from column definitions and row data.
   * Columns can be strings (used as both key and header) or objects with label/key.
   * Rows can be arrays (positional) or objects (keyed by column).
   */
  factories.Table = function (props) {
    var cols = props.columns || [];
    var rows = props.rows || [];
    var table = el("table", "a2ui-table");
    if (cols.length) {
      var thead = el("thead");
      var tr = el("tr");
      cols.forEach(function (c) { var th = el("th"); txt(th, typeof c === "string" ? c : c.label || ""); tr.appendChild(th); });
      thead.appendChild(tr); table.appendChild(thead);
    }
    var tbody = el("tbody");
    rows.forEach(function (row) {
      var tr = el("tr");
      if (Array.isArray(row)) {
        row.forEach(function (cell) { var td = el("td"); txt(td, String(cell != null ? cell : "")); tr.appendChild(td); });
      } else {
        cols.forEach(function (c) {
          var key = typeof c === "string" ? c : c.key || c.label || "";
          var td = el("td"); txt(td, String(row[key] != null ? row[key] : "")); tr.appendChild(td);
        });
      }
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return table;
  };

  /**
   * Stepper — multi-step progress indicator.
   * Each step has a label and status: "done" (green check), "active" (accent), or "pending" (grey).
   */
  factories.Stepper = function (props, _c, _a, dark) {
    var steps = props.steps || [];
    var w = el("div", "a2ui-stepper", { role: "navigation", "aria-label": "Progress steps" });
    steps.forEach(function (s, i) {
      if (i > 0) { w.appendChild(el("div", "a2ui-stepper__line")); }
      var step = el("div", "a2ui-stepper__step");
      var dot = el("div", "a2ui-stepper__dot");
      var status = s.status || "pending";
      if (status === "done") { dot.style.background = toneColor("success", dark); dot.textContent = "✓"; }
      else if (status === "active") { dot.style.background = toneColor("accent", dark); dot.textContent = String(i + 1); }
      else { dot.style.background = "var(--border)"; dot.textContent = String(i + 1); }
      step.appendChild(dot);
      var lbl = el("span"); txt(lbl, s.label || "Step " + (i + 1)); step.appendChild(lbl);
      w.appendChild(step);
    });
    return w;
  };

  /** Checklist — interactive checkbox list. Clicking toggles items and fires onAction. */
  factories.Checklist = function (props, _c, onAction) {
    var w = el("div", "a2ui-checklist");
    if (props.title) { var t = el("div", "a2ui-checklist__title"); txt(t, props.title); w.appendChild(t); }
    (props.items || []).forEach(function (item, i) {
      var row = el("div", "a2ui-checklist__item");
      var box = el("div", "a2ui-checklist__box" + (item.done ? " a2ui-checklist__box--done" : ""));
      if (item.done) box.textContent = "✓";
      row.appendChild(box);
      var lbl = el("span"); txt(lbl, item.label || ""); row.appendChild(lbl);
      row.style.cursor = "pointer";
      row.addEventListener("click", function () {
        item.done = !item.done;
        box.className = "a2ui-checklist__box" + (item.done ? " a2ui-checklist__box--done" : "");
        box.textContent = item.done ? "✓" : "";
        if (onAction) onAction("checklist:toggle:" + i);
      });
      w.appendChild(row);
    });
    return w;
  };

  /* ── Interactive ── */

  /** Button — clickable element that fires onAction with the `action` string. */
  factories.Button = function (props, _c, onAction) {
    var b = el("button", "a2ui-btn");
    txt(b, props.label || "Click");
    b.addEventListener("click", function () { if (onAction) onAction(props.action || props.label); });
    return b;
  };

  /**
   * Form — container that gathers child input values on submit.
   * Fires onAction with JSON string: { form: action, data: { name: value, ... } }
   */
  factories.Form = function (props, children, onAction) {
    var f = el("form", "a2ui-form");
    f.addEventListener("submit", function (e) {
      e.preventDefault();
      var data = {};
      var inputs = f.querySelectorAll("input, select");
      inputs.forEach(function (inp) {
        if (inp.type === "checkbox") data[inp.name] = inp.checked;
        else data[inp.name] = inp.value;
      });
      if (onAction) onAction(JSON.stringify({ form: props.action || "submit", data: data }));
    });
    if (props.title) { var t = el("div", "a2ui-form__title"); txt(t, props.title); f.appendChild(t); }
    children.forEach(function (ch) { f.appendChild(ch); });
    var btn = el("button", "a2ui-btn");
    btn.type = "submit";
    txt(btn, props.submitLabel || "Submit");
    f.appendChild(btn);
    return f;
  };

  /** Input — text input field with name, label, placeholder. */
  factories.Input = function (props) {
    var w = el("div", "a2ui-input");
    if (props.label) { var l = el("label", "a2ui-input__label"); txt(l, props.label); w.appendChild(l); }
    var inp = el("input", "a2ui-input__field");
    inp.type = props.type || "text";
    inp.name = props.name || "";
    inp.placeholder = props.placeholder || "";
    if (props.value != null) inp.value = props.value;
    w.appendChild(inp);
    return w;
  };

  /** Select — dropdown with name, label, and options array. */
  factories.Select = function (props) {
    var w = el("div", "a2ui-select");
    if (props.label) { var l = el("label", "a2ui-select__label"); txt(l, props.label); w.appendChild(l); }
    var sel = el("select", "a2ui-select__field");
    sel.name = props.name || "";
    (props.options || []).forEach(function (opt) {
      var o = el("option");
      o.value = typeof opt === "string" ? opt : opt.value || "";
      txt(o, typeof opt === "string" ? opt : opt.label || opt.value || "");
      sel.appendChild(o);
    });
    w.appendChild(sel);
    return w;
  };

  /** Checkbox — toggle with custom styled box. Fires onAction on change. */
  factories.Checkbox = function (props, _c, onAction) {
    var w = el("label", "a2ui-checkbox");
    var checked = !!props.checked;
    var box = el("div", "a2ui-checkbox__box" + (checked ? " a2ui-checkbox__box--on" : ""));
    if (checked) box.textContent = "✓";
    // Hidden native checkbox for form submission
    var inp = el("input");
    inp.type = "checkbox";
    inp.name = props.name || "";
    inp.checked = checked;
    inp.style.display = "none";
    w.appendChild(inp);
    w.appendChild(box);
    var lbl = el("span"); txt(lbl, props.label || ""); w.appendChild(lbl);
    w.addEventListener("click", function (e) {
      e.preventDefault();
      inp.checked = !inp.checked;
      box.className = "a2ui-checkbox__box" + (inp.checked ? " a2ui-checkbox__box--on" : "");
      box.textContent = inp.checked ? "✓" : "";
      if (onAction) onAction("checkbox:" + (props.name || "") + ":" + inp.checked);
    });
    return w;
  };

  /* ── Calendar ── */

  /**
   * Calendar — month grid with day-of-week headers and event dots.
   * Events are matched by date and displayed as small coloured dots.
   * @param {{year: number, month: number, events: Array<{date: string, label: string, tone?: string}>}} props
   */
  factories.Calendar = function (props, _c, _a, dark) {
    var year = props.year || new Date().getFullYear();
    var month = props.month != null ? props.month : new Date().getMonth() + 1;
    var events = props.events || [];
    var MONTHS = ["January", "February", "March", "April", "May", "June",
                  "July", "August", "September", "October", "November", "December"];

    var w = el("div", "a2ui-calendar");
    var title = el("div", "a2ui-calendar__title");
    txt(title, MONTHS[month - 1] + " " + year);
    w.appendChild(title);

    var grid = el("div", "a2ui-calendar__grid");
    ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"].forEach(function (d) {
      var dw = el("div", "a2ui-calendar__dow"); txt(dw, d); grid.appendChild(dw);
    });

    var firstDay = new Date(year, month - 1, 1).getDay();
    var daysInMonth = new Date(year, month, 0).getDate();
    var today = new Date();

    // Build event lookup by day
    var evByDay = {};
    events.forEach(function (ev) {
      var d = new Date(ev.date);
      if (d.getFullYear() === year && d.getMonth() === month - 1) {
        var key = d.getDate();
        if (!evByDay[key]) evByDay[key] = [];
        evByDay[key].push(ev);
      }
    });

    for (var i = 0; i < firstDay; i++) grid.appendChild(el("div", "a2ui-calendar__day"));
    for (var d = 1; d <= daysInMonth; d++) {
      var cell = el("div", "a2ui-calendar__day");
      if (d === today.getDate() && month === today.getMonth() + 1 && year === today.getFullYear()) {
        cell.className += " a2ui-calendar__day--today";
      }
      txt(cell, String(d));
      if (evByDay[d]) {
        var dots = el("div");
        evByDay[d].forEach(function (ev) {
          var dot = el("span", "a2ui-calendar__event");
          dot.style.background = toneColor(ev.tone || "accent", dark);
          dot.title = ev.label || "";
          dots.appendChild(dot);
        });
        cell.appendChild(dots);
      }
      grid.appendChild(cell);
    }
    w.appendChild(grid);
    return w;
  };

  /* ── Modal & Drawer ── */

  /** Modal — overlay dialog with title, close button, and child content. */
  factories.Modal = function (props, children, onAction) {
    var overlay = el("div", "a2ui-modal-overlay");
    var modal = el("div", "a2ui-modal");
    var header = el("div", "a2ui-modal__header");
    var title = el("span", "a2ui-modal__title"); txt(title, props.title || "");
    var closeBtn = el("button", "a2ui-modal__close"); txt(closeBtn, "✕");
    closeBtn.addEventListener("click", function () { if (onAction) onAction("close_modal"); });
    header.appendChild(title); header.appendChild(closeBtn);
    modal.appendChild(header);
    children.forEach(function (ch) { modal.appendChild(ch); });
    overlay.appendChild(modal);
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay && onAction) onAction("close_modal");
    });
    return overlay;
  };

  /** Drawer — slide-in panel from left or right with title and close button. */
  factories.Drawer = function (props, children, onAction) {
    var frag = el("div");
    frag.style.display = "contents";
    var overlay = el("div", "a2ui-drawer-overlay");
    overlay.addEventListener("click", function () { if (onAction) onAction("close_modal"); });
    frag.appendChild(overlay);
    var side = props.side === "left" ? "left" : "right";
    var drawer = el("div", "a2ui-drawer a2ui-drawer--" + side);
    var header = el("div", "a2ui-drawer__header");
    var title = el("span", "a2ui-drawer__title"); txt(title, props.title || "");
    var closeBtn = el("button", "a2ui-drawer__close"); txt(closeBtn, "✕");
    closeBtn.addEventListener("click", function () { if (onAction) onAction("close_modal"); });
    header.appendChild(title); header.appendChild(closeBtn);
    drawer.appendChild(header);
    var body = el("div", "a2ui-drawer__body");
    children.forEach(function (ch) { body.appendChild(ch); });
    drawer.appendChild(body);
    frag.appendChild(drawer);
    return frag;
  };

  /* ── Additional Charts ── */

  /** LineChart — CSS-based line chart using SVG polyline. */
  factories.LineChart = function (props, _c, _a, dark) {
    var data = props.data || [];
    var w = el("div", "a2ui-linechart");
    if (props.title) { var t = el("div", "a2ui-linechart__title"); txt(t, props.title); w.appendChild(t); }
    var values = data.map(function (d) { return d.value; });
    var maxVal = Math.max.apply(null, values.concat([1]));
    var minVal = Math.min.apply(null, values.concat([0]));
    var range = maxVal - minVal || 1;
    var svgW = 300; var svgH = 120; var pad = 4;
    var points = data.map(function (d, i) {
      var x = data.length > 1 ? pad + (i / (data.length - 1)) * (svgW - pad * 2) : svgW / 2;
      var y = pad + (1 - (d.value - minVal) / range) * (svgH - pad * 2);
      return x + "," + y;
    }).join(" ");
    var accentColor = toneColor("accent", dark);
    var svgStr = '<svg class="a2ui-linechart__svg" viewBox="0 0 ' + svgW + ' ' + svgH + '" preserveAspectRatio="none">'
      + '<polyline points="' + points + '" fill="none" stroke="' + accentColor + '" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>';
    data.forEach(function (d, i) {
      var x = data.length > 1 ? pad + (i / (data.length - 1)) * (svgW - pad * 2) : svgW / 2;
      var y = pad + (1 - (d.value - minVal) / range) * (svgH - pad * 2);
      svgStr += '<circle cx="' + x + '" cy="' + y + '" r="3.5" fill="' + accentColor + '"/>';
    });
    svgStr += '</svg>';
    var svgContainer = el("div"); svgContainer.innerHTML = svgStr;
    w.appendChild(svgContainer.firstChild);
    var labels = el("div", "a2ui-linechart__labels");
    data.forEach(function (d) { var s = el("span"); txt(s, d.label || ""); labels.appendChild(s); });
    w.appendChild(labels);
    return w;
  };

  /** Sparkline — compact inline SVG line. */
  factories.Sparkline = function (props, _c, _a, dark) {
    var values = props.values || [];
    var w = el("span", "a2ui-sparkline");
    if (props.label) { var l = el("span", "a2ui-sparkline__label"); txt(l, props.label); w.appendChild(l); }
    var maxVal = Math.max.apply(null, values.concat([1]));
    var minVal = Math.min.apply(null, values.concat([0]));
    var range = maxVal - minVal || 1;
    var svgW = 80; var svgH = 20;
    var points = values.map(function (v, i) {
      var x = values.length > 1 ? (i / (values.length - 1)) * svgW : svgW / 2;
      var y = 2 + (1 - (v - minVal) / range) * (svgH - 4);
      return x + "," + y;
    }).join(" ");
    var accentColor = toneColor("accent", dark);
    var svgStr = '<svg width="' + svgW + '" height="' + svgH + '" viewBox="0 0 ' + svgW + ' ' + svgH + '">'
      + '<polyline points="' + points + '" fill="none" stroke="' + accentColor + '" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>'
      + '</svg>';
    var svgContainer = el("div"); svgContainer.style.display = "inline-block"; svgContainer.innerHTML = svgStr;
    w.appendChild(svgContainer.firstChild);
    return w;
  };

  /* ── Media ── */

  /** Image — img tag with optional caption. */
  factories.Image = function (props) {
    var w = el("div", "a2ui-image");
    var img = el("img", "a2ui-image__img", { src: props.src || "", alt: props.alt || "" });
    w.appendChild(img);
    if (props.caption) { var cap = el("div", "a2ui-image__caption"); txt(cap, props.caption); w.appendChild(cap); }
    return w;
  };

  /** Avatar — circular image or initials fallback. */
  factories.Avatar = function (props) {
    var w = el("div", "a2ui-avatar");
    if (props.src) {
      var img = el("img", "a2ui-avatar__img", { src: props.src, alt: props.name || "" });
      w.appendChild(img);
    } else {
      var name = props.name || "?";
      var initials = name.split(/\s+/).map(function (p) { return p.charAt(0).toUpperCase(); }).join("").substring(0, 2);
      txt(w, initials);
    }
    return w;
  };

  /* ── Additional Interactive ── */

  /** Toggle — on/off switch with label. */
  factories.Toggle = function (props, _c, onAction) {
    var checked = !!props.checked;
    var w = el("div", "a2ui-toggle");
    var track = el("div", "a2ui-toggle__track" + (checked ? " a2ui-toggle__track--on" : ""));
    var thumb = el("div", "a2ui-toggle__thumb");
    track.appendChild(thumb);
    w.appendChild(track);
    if (props.label) { var l = el("span", "a2ui-toggle__label"); txt(l, props.label); w.appendChild(l); }
    w.addEventListener("click", function () {
      checked = !checked;
      track.className = "a2ui-toggle__track" + (checked ? " a2ui-toggle__track--on" : "");
      if (onAction) onAction("toggle:" + (props.label || "") + ":" + checked);
    });
    return w;
  };

  /** Slider — range input with name, label, min, max, value. */
  factories.Slider = function (props) {
    var w = el("div", "a2ui-slider");
    if (props.label) { var l = el("label", "a2ui-slider__label"); txt(l, props.label); w.appendChild(l); }
    var inp = el("input", "a2ui-slider__field", {
      type: "range",
      name: props.name || "",
      min: String(props.min != null ? props.min : 0),
      max: String(props.max != null ? props.max : 100)
    });
    if (props.value != null) inp.value = String(props.value);
    w.appendChild(inp);
    return w;
  };

  /** Chip — clickable pill that sends text as action. */
  factories.Chip = function (props, _c, onAction) {
    var w = el("span", "a2ui-chip");
    txt(w, props.label || "");
    w.addEventListener("click", function () { if (onAction) onAction(props.text || props.label || ""); });
    return w;
  };

  /** ChatInput — text input that sends value as action on Enter. */
  factories.ChatInput = function (props, _c, onAction) {
    var w = el("div", "a2ui-chatinput");
    var inp = el("input", "a2ui-chatinput__field", { placeholder: props.placeholder || "Type a message…" });
    var btn = el("button", "a2ui-chatinput__btn"); txt(btn, props.submitLabel || "Send");
    function submit() {
      var val = inp.value.trim();
      if (val && onAction) { onAction(val); inp.value = ""; }
    }
    inp.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); submit(); } });
    btn.addEventListener("click", submit);
    w.appendChild(inp); w.appendChild(btn);
    return w;
  };

  /** ThemeToggle — dark/light mode toggle. Toggles data-theme on the a2ui-root container. */
  factories.ThemeToggle = function (props) {
    var w = el("div", "a2ui-themetoggle");
    var icon = el("span", "a2ui-themetoggle__icon"); icon.textContent = "☀️";
    w.appendChild(icon);
    if (props.label) { var l = el("span"); txt(l, props.label); w.appendChild(l); }
    w.addEventListener("click", function () {
      var root = w.closest(".a2ui-root");
      if (!root) return;
      var current = root.getAttribute("data-theme");
      var next = current === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      icon.textContent = next === "dark" ? "🌙" : "☀️";
    });
    return w;
  };

  /* ── Navigation ── */

  /** Breadcrumb — navigation trail with clickable items. */
  factories.Breadcrumb = function (props, _c, onAction) {
    var items = props.items || [];
    var w = el("nav", "a2ui-breadcrumb", { "aria-label": "Breadcrumb" });
    items.forEach(function (item, i) {
      if (i > 0) { var sep = el("span", "a2ui-breadcrumb__sep"); txt(sep, "/"); w.appendChild(sep); }
      if (item.action) {
        var a = el("span", "a2ui-breadcrumb__item a2ui-breadcrumb__item--link");
        txt(a, item.label || "");
        a.addEventListener("click", function () { if (onAction) onAction(item.action); });
        w.appendChild(a);
      } else {
        var s = el("span", "a2ui-breadcrumb__item"); txt(s, item.label || ""); w.appendChild(s);
      }
    });
    return w;
  };

  /** EmptyState — placeholder with icon, title, message, and optional action button. */
  factories.EmptyState = function (props, _c, onAction) {
    var w = el("div", "a2ui-emptystate");
    if (props.icon) { var ic = el("div", "a2ui-emptystate__icon"); txt(ic, props.icon); w.appendChild(ic); }
    if (props.title) { var t = el("div", "a2ui-emptystate__title"); txt(t, props.title); w.appendChild(t); }
    if (props.message) { var m = el("div", "a2ui-emptystate__msg"); txt(m, props.message); w.appendChild(m); }
    if (props.actionLabel) {
      var btn = el("button", "a2ui-btn"); txt(btn, props.actionLabel);
      btn.addEventListener("click", function () { if (onAction) onAction(props.action || props.actionLabel); });
      w.appendChild(btn);
    }
    return w;
  };

  /** Tabs — tabbed panels. First tab active by default. */
  factories.Tabs = function (props, _c, onAction, dark) {
    var tabs = props.tabs || [];
    var w = el("div", "a2ui-tabs");
    var nav = el("div", "a2ui-tabs__nav");
    var panels = [];
    tabs.forEach(function (tab, i) {
      var btn = el("button", "a2ui-tabs__tab" + (i === 0 ? " a2ui-tabs__tab--active" : ""));
      txt(btn, tab.label || "Tab " + (i + 1));
      nav.appendChild(btn);
      var panel = el("div", "a2ui-tabs__panel" + (i === 0 ? " a2ui-tabs__panel--active" : ""));
      if (tab.content) { var p = el("div"); txt(p, tab.content); panel.appendChild(p); }
      if (tab.children && Array.isArray(tab.children)) {
        tab.children.forEach(function (childDef) {
          if (typeof childDef === "string") {
            var p = el("p"); txt(p, childDef); panel.appendChild(p);
          } else if (childDef && childDef.type && factories[childDef.type]) {
            var childNode = factories[childDef.type](childDef.props || {}, [], onAction, dark);
            panel.appendChild(childNode);
          }
        });
      }
      panels.push(panel);
      btn.addEventListener("click", function () {
        nav.querySelectorAll(".a2ui-tabs__tab").forEach(function (b) { b.classList.remove("a2ui-tabs__tab--active"); });
        panels.forEach(function (p) { p.classList.remove("a2ui-tabs__panel--active"); });
        btn.classList.add("a2ui-tabs__tab--active");
        panel.classList.add("a2ui-tabs__panel--active");
      });
    });
    w.appendChild(nav);
    panels.forEach(function (p) { w.appendChild(p); });
    return w;
  };

  /** Accordion — expandable sections with title and content. */
  factories.Accordion = function (props) {
    var items = props.items || [];
    var w = el("div", "a2ui-accordion");
    items.forEach(function (item) {
      var section = el("div", "a2ui-accordion__item");
      var header = el("div", "a2ui-accordion__header");
      var label = el("span"); txt(label, item.title || "");
      var arrow = el("span", "a2ui-accordion__arrow"); txt(arrow, "▶");
      header.appendChild(label); header.appendChild(arrow);
      var body = el("div", "a2ui-accordion__body");
      if (item.content) txt(body, item.content);
      header.addEventListener("click", function () {
        var open = body.classList.toggle("a2ui-accordion__body--open");
        arrow.className = "a2ui-accordion__arrow" + (open ? " a2ui-accordion__arrow--open" : "");
      });
      section.appendChild(header); section.appendChild(body);
      w.appendChild(section);
    });
    return w;
  };

  /* ──────────────────── Core render engine ───────────────────────────── */

  /** Clock — live analog/digital clock that auto-updates. */
  factories.Clock = function (props) {
    var w = el("div", "a2ui-clock");
    w.style.cssText = "text-align:center;padding:16px;";
    var timeEl = el("div");
    timeEl.style.cssText = "font-size:2.5rem;font-weight:700;font-family:monospace;color:#2c2820;letter-spacing:2px;";
    var dateEl = el("div");
    dateEl.style.cssText = "font-size:0.85rem;color:#8a8070;margin-top:4px;";
    w.appendChild(timeEl);
    w.appendChild(dateEl);
    function tick() {
      var now = new Date();
      timeEl.textContent = now.toLocaleTimeString("en-US", {hour:"2-digit",minute:"2-digit",second:"2-digit"});
      dateEl.textContent = now.toLocaleDateString("en-US", {weekday:"long",year:"numeric",month:"long",day:"numeric"});
    }
    tick();
    setInterval(tick, 1000);
    return w;
  };

  /** Map — embedded map using OpenStreetMap iframe. */
  factories.Map = function (props) {
    var lat = props.lat || 12.9716;
    var lng = props.lng || 77.5946;
    var zoom = props.zoom || 13;
    var title = props.title || "";
    var w = el("div", "a2ui-map");
    if (title) {
      var h = el("div");
      h.style.cssText = "font-size:0.85rem;font-weight:600;margin-bottom:8px;color:#2c2820;";
      txt(h, title);
      w.appendChild(h);
    }
    var iframe = document.createElement("iframe");
    iframe.src = "https://www.openstreetmap.org/export/embed.html?bbox=" +
      (lng-0.05) + "," + (lat-0.03) + "," + (lng+0.05) + "," + (lat+0.03) +
      "&layer=mapnik&marker=" + lat + "," + lng;
    iframe.style.cssText = "width:100%;height:250px;border:1px solid #e8e0d0;border-radius:8px;";
    iframe.setAttribute("loading", "lazy");
    w.appendChild(iframe);
    // Add markers if provided
    if (props.markers && props.markers.length) {
      var list = el("div");
      list.style.cssText = "margin-top:8px;font-size:0.8rem;color:#8a8070;";
      props.markers.forEach(function(m) {
        var item = el("div");
        txt(item, "📍 " + (m.label || "") + (m.description ? " — " + m.description : ""));
        list.appendChild(item);
      });
      w.appendChild(list);
    }
    return w;
  };

  /**
   * Build a component by id from the component map.
   * @param {string} id
   * @param {Object<string, Object>} compMap  id → component definition
   * @param {function} onAction
   * @param {boolean} dark
   * @returns {HTMLElement}
   */
  function buildComponent(id, compMap, onAction, dark) {
    var def = compMap[id];
    if (!def) {
      var placeholder = el("div");
      placeholder.style.color = "red";
      txt(placeholder, "[A2UI: unknown component '" + id + "']");
      placeholder.setAttribute("data-aid", id);
      return placeholder;
    }

    var factory = factories[def.type];
    if (!factory) {
      var unk = el("div");
      unk.style.color = "orange";
      txt(unk, "[A2UI: unknown type '" + def.type + "']");
      unk.setAttribute("data-aid", id);
      return unk;
    }

    // Recursively build children
    var childEls = (def.children || []).map(function (cid) {
      return buildComponent(cid, compMap, onAction, dark);
    });

    var node = factory(def.props || {}, childEls, onAction, dark);
    node.setAttribute("data-aid", id);
    return node;
  }

  /* ──────────────────── Public API ──────────────────────────────────── */

  /**
   * Render an A2UI document into a container element.
   * @param {HTMLElement} container - DOM element to mount into
   * @param {Object} doc - A2UI JSON document {surface, root, components}
   * @param {function} [onAction] - Callback receiving action strings
   */
  window.renderA2UI = function (container, doc, onAction) {
    injectStyles();

    // Build component lookup map
    var compMap = {};
    (doc.components || []).forEach(function (c) { compMap[c.id] = c; });

    // Determine dark mode
    var dark = isDark(container);

    // Wrap in root class
    container.innerHTML = "";
    var root = el("div", "a2ui-root");
    if (container.getAttribute("data-theme") === "dark") root.setAttribute("data-theme", "dark");
    root.style.background = "var(--bg)";
    root.style.padding = "16px";

    var tree = buildComponent(doc.root, compMap, onAction || function () {}, dark || root.getAttribute("data-theme") === "dark");
    root.appendChild(tree);
    container.appendChild(root);
  };

  /**
   * Patch a rendered A2UI tree with incremental operations.
   * Supported operations:
   *   - "replace": Replace the target element with a new component
   *   - "append":  Append a new component as a child of the target
   *   - "remove":  Remove the target element from the DOM
   *
   * @param {HTMLElement} container - The same container passed to renderA2UI
   * @param {Array<Object>} ops - [{op:"replace"|"append"|"remove", targetId, component?, components?}]
   * @param {function} [onAction] - Optional callback for interactive elements in patched components
   */
  window.patchA2UI = function (container, ops, onAction) {
    var dark = isDark(container);
    var cb = onAction || function () {};

    ops.forEach(function (op) {
      var target = container.querySelector('[data-aid="' + op.targetId + '"]');
      if (!target) {
        console.warn("[A2UI patch] target not found:", op.targetId);
        return;
      }

      if (op.op === "remove") {
        target.parentNode.removeChild(target);
        return;
      }

      if (op.component) {
        // Build a mini compMap; include additional components for children
        var miniMap = {};
        miniMap[op.component.id] = op.component;
        if (op.components) {
          op.components.forEach(function (c) { miniMap[c.id] = c; });
        }
        var newEl = buildComponent(op.component.id, miniMap, cb, dark);

        if (op.op === "replace") {
          target.parentNode.replaceChild(newEl, target);
        } else if (op.op === "append") {
          target.appendChild(newEl);
        }
      }
    });
  };

  /**
   * Remove all A2UI content from a container, cleaning up any rendered tree.
   * Useful when unmounting in a React useEffect cleanup.
   * @param {HTMLElement} container - The container previously passed to renderA2UI
   */
  window.destroyA2UI = function (container) {
    container.innerHTML = "";
  };
})();
