// ==UserScript==
// @name         160Grab 91160 Doctor Page Poller
// @namespace    https://github.com/wufei-png/160Grab
// @version      0.2.7
// @description  Poll a real 91160 doctor detail page, jump into ystep1, and optionally submit the booking form.
// @author       OpenAI Codex
// @match        https://www.91160.com/doctors/index/*
// @match        https://www.91160.com/guahao/ystep1/*
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_deleteValue
// @grant        unsafeWindow
// @run-at       document-idle
// ==/UserScript==

(function () {
  "use strict";

  const SETTINGS_KEY = "grab160.doctorPagePoller.settings.v2";
  const STATE_KEY = "grab160.doctorPagePoller.state.v2";
  const PANEL_POSITION_KEY = "grab160.doctorPagePoller.panelPosition.v2";
  const PANEL_ID = "grab160-doctor-page-poller-panel";
  const SCRIPT_VERSION = "0.2.7";
  const PLACEHOLDER_VALUES = new Set(["", "...", "null", "undefined", "<member_id>"]);
  const RATE_LIMIT_PATTERNS = [
    "单位时间内访问次数过多",
    "访问次数过多",
    "访问过于频繁",
    "操作过于频繁",
  ];
  const LOG_LEVELS = ["debug", "info", "warn", "error"];
  const DEFAULT_DISEASE_DESCRIPTION = "门诊就诊，具体病情现场面诊沟通";
  const DISABLE_AUTO_START = Boolean(
    globalThis.__GRAB160_DOCTOR_POLLER_DISABLE_AUTO_START__,
  );
  let lastResolvedUserKey = null;
  let lastResolvedUserKeySource = null;
  let activeControllerId = null;

  const CONFIG_DEFAULTS = {
    runtime: {
      autoStart: false,
      startAt: null,
    },
    target: {
      unitId: null,
      depId: null,
      doctorId: null,
    },
    member: {
      memberId: null,
      memberLabel: null,
    },
    address: {
      province: "广东",
      city: "深圳",
      area: "南山区",
      detail: null,
    },
    filters: {
      startDate: null,
      weeks: [],
      days: [],
      hours: [],
    },
    pacing: {
      pollMs: [3000, 5000],
      pageActionMs: [400, 900],
      bookingRetryMs: [2000, 4000],
      rateLimitCooldownMs: [15000, 25000],
    },
    booking: {
      autoSubmit: false,
      maxSubmitAttemptsPerAppointment: 3,
      autoReturnAfterSubmitFailure: false,
      diseaseDescription: DEFAULT_DISEASE_DESCRIPTION,
    },
    session: {
      recoveryEnabled: true,
      keepAliveIntervalSeconds: 240,
      recoveryMaxAttempts: 3,
      recoveryCooldownMs: [3000, 8000],
    },
    logging: {
      level: "info",
      maxEntries: 100,
    },
  };

  function compactText(value) {
    return String(value ?? "").replace(/\s+/g, " ").trim();
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function isPlainObject(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  function mergeConfig(base, override) {
    const output = clone(base);
    if (!isPlainObject(override)) {
      return output;
    }
    for (const [key, value] of Object.entries(override)) {
      if (isPlainObject(value) && isPlainObject(output[key])) {
        output[key] = mergeConfig(output[key], value);
      } else if (value !== undefined) {
        output[key] = value;
      }
    }
    return output;
  }

  function normalizeOptionalValue(value) {
    const text = compactText(value);
    return PLACEHOLDER_VALUES.has(text.toLowerCase()) ? null : text;
  }

  function normalizeBoolean(value, fallback = false) {
    return typeof value === "boolean" ? value : fallback;
  }

  function normalizeInteger(value, fallback, { min = null, max = null } = {}) {
    const parsed = Number.parseInt(String(value), 10);
    if (!Number.isInteger(parsed)) {
      return fallback;
    }
    if (min !== null && parsed < min) {
      return fallback;
    }
    if (max !== null && parsed > max) {
      return fallback;
    }
    return parsed;
  }

  function normalizeRange(value, fallback, { min = 0 } = {}) {
    const source = Array.isArray(value) && value.length === 2 ? value : fallback;
    const first = Math.max(min, Number(source[0]));
    const second = Math.max(min, Number(source[1]));
    if (!Number.isFinite(first) || !Number.isFinite(second)) {
      return clone(fallback);
    }
    return first <= second ? [Math.round(first), Math.round(second)] : [Math.round(second), Math.round(first)];
  }

  function normalizeDateValue(value) {
    const text = compactText(value);
    return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text : null;
  }

  function normalizeStartAtValue(value) {
    const text = compactText(value);
    if (!text) {
      return null;
    }
    const timestamp = Date.parse(text);
    if (Number.isNaN(timestamp)) {
      return null;
    }
    return text;
  }

  function normalizeHourEndpoint(value) {
    const text = compactText(value);
    const integerMatch = text.match(/^(\d{1,2})$/);
    if (integerMatch) {
      return `${String(Number(integerMatch[1])).padStart(2, "0")}:00`;
    }
    const halfHourMatch = text.match(/^(\d{1,2})\.(0|5)$/);
    if (halfHourMatch) {
      return `${String(Number(halfHourMatch[1])).padStart(2, "0")}:${
        halfHourMatch[2] === "5" ? "30" : "00"
      }`;
    }
    const preciseMatch = text.match(/^(\d{1,2}):(\d{2})$/);
    if (preciseMatch) {
      const hour = Number(preciseMatch[1]);
      const minute = Number(preciseMatch[2]);
      if (hour < 0 || hour > 23 || ![0, 30].includes(minute)) {
        throw new Error("Hour endpoints must use 00 or 30 minute precision.");
      }
      return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
    }
    throw new Error("Invalid hour format.");
  }

  function normalizeHourValue(value) {
    const text = compactText(value);
    const parts = text.split("-");
    if (parts.length !== 2) {
      throw new Error("Invalid hour format. Use HH:MM-HH:MM, H-H, H.5-H.");
    }
    const start = normalizeHourEndpoint(parts[0]);
    const end = normalizeHourEndpoint(parts[1]);
    if (parseTimeToMinutes(start) >= parseTimeToMinutes(end)) {
      throw new Error("Hour range start must be earlier than end.");
    }
    return `${start}-${end}`;
  }

  function normalizeHours(values) {
    if (!Array.isArray(values)) {
      return [];
    }
    return values.map((value) => normalizeHourValue(value));
  }

  function normalizeSettings(rawSettings) {
    const merged = mergeConfig(CONFIG_DEFAULTS, rawSettings);
    return {
      runtime: {
        autoStart: normalizeBoolean(merged.runtime.autoStart, false),
        startAt: normalizeStartAtValue(merged.runtime.startAt),
      },
      target: {
        unitId: normalizeOptionalValue(merged.target.unitId),
        depId: normalizeOptionalValue(merged.target.depId),
        doctorId: normalizeOptionalValue(merged.target.doctorId),
      },
      member: {
        memberId: normalizeOptionalValue(merged.member.memberId),
        memberLabel: normalizeOptionalValue(merged.member.memberLabel),
      },
      address: {
        province: normalizeOptionalValue(merged.address.province),
        city: normalizeOptionalValue(merged.address.city),
        area: normalizeOptionalValue(merged.address.area),
        detail: normalizeOptionalValue(merged.address.detail),
      },
      filters: {
        startDate: normalizeDateValue(merged.filters.startDate),
        weeks: Array.isArray(merged.filters.weeks)
          ? merged.filters.weeks
              .map((value) => Number.parseInt(String(value), 10))
              .filter((value) => Number.isInteger(value) && value >= 1 && value <= 7)
          : [],
        days: Array.isArray(merged.filters.days)
          ? merged.filters.days
              .map((value) => compactText(value).toLowerCase())
              .filter((value) => ["am", "pm", "em"].includes(value))
          : [],
        hours: normalizeHours(merged.filters.hours),
      },
      pacing: {
        pollMs: normalizeRange(merged.pacing.pollMs, CONFIG_DEFAULTS.pacing.pollMs, {
          min: CONFIG_DEFAULTS.pacing.pollMs[0],
        }),
        pageActionMs: normalizeRange(
          merged.pacing.pageActionMs,
          CONFIG_DEFAULTS.pacing.pageActionMs,
        ),
        bookingRetryMs: normalizeRange(
          merged.pacing.bookingRetryMs,
          CONFIG_DEFAULTS.pacing.bookingRetryMs,
          { min: 1000 },
        ),
        rateLimitCooldownMs: normalizeRange(
          merged.pacing.rateLimitCooldownMs,
          CONFIG_DEFAULTS.pacing.rateLimitCooldownMs,
          { min: CONFIG_DEFAULTS.pacing.rateLimitCooldownMs[0] },
        ),
      },
      booking: {
        autoSubmit: normalizeBoolean(merged.booking.autoSubmit, false),
        maxSubmitAttemptsPerAppointment: normalizeInteger(
          merged.booking.maxSubmitAttemptsPerAppointment,
          CONFIG_DEFAULTS.booking.maxSubmitAttemptsPerAppointment,
          { min: 1, max: 20 },
        ),
        autoReturnAfterSubmitFailure: normalizeBoolean(
          merged.booking.autoReturnAfterSubmitFailure,
          false,
        ),
        diseaseDescription:
          normalizeOptionalValue(merged.booking.diseaseDescription) ??
          DEFAULT_DISEASE_DESCRIPTION,
      },
      session: {
        recoveryEnabled: normalizeBoolean(merged.session.recoveryEnabled, true),
        keepAliveIntervalSeconds: normalizeInteger(
          merged.session.keepAliveIntervalSeconds,
          CONFIG_DEFAULTS.session.keepAliveIntervalSeconds,
          { min: 0 },
        ),
        recoveryMaxAttempts: normalizeInteger(
          merged.session.recoveryMaxAttempts,
          CONFIG_DEFAULTS.session.recoveryMaxAttempts,
          { min: 0, max: 20 },
        ),
        recoveryCooldownMs: normalizeRange(
          merged.session.recoveryCooldownMs,
          CONFIG_DEFAULTS.session.recoveryCooldownMs,
        ),
      },
      logging: {
        level: LOG_LEVELS.includes(merged.logging.level)
          ? merged.logging.level
          : CONFIG_DEFAULTS.logging.level,
        maxEntries: normalizeInteger(merged.logging.maxEntries, 100, {
          min: 10,
          max: 500,
        }),
      },
    };
  }

  function readStoredValue(key, fallback) {
    try {
      if (typeof GM_getValue === "function") {
        return GM_getValue(key, fallback);
      }
    } catch (_error) {
      // Fall back to browser storage below.
    }
    try {
      const raw = globalThis.localStorage?.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (_error) {
      return fallback;
    }
  }

  function writeStoredValue(key, value) {
    try {
      if (typeof GM_setValue === "function") {
        GM_setValue(key, value);
        return;
      }
    } catch (_error) {
      // Fall back to browser storage below.
    }
    globalThis.localStorage?.setItem(key, JSON.stringify(value));
  }

  function deleteStoredValue(key) {
    try {
      if (typeof GM_deleteValue === "function") {
        GM_deleteValue(key);
        return;
      }
    } catch (_error) {
      // Fall back to browser storage below.
    }
    globalThis.localStorage?.removeItem(key);
  }

  function readSettings() {
    try {
      return normalizeSettings(readStoredValue(SETTINGS_KEY, null));
    } catch (error) {
      appendLog("error", "Failed to load stored settings; using defaults.", error.message);
      return normalizeSettings(null);
    }
  }

  function writeSettings(settings) {
    const normalized = normalizeSettings(settings);
    writeStoredValue(SETTINGS_KEY, normalized);
    return normalized;
  }

  function resetSettings() {
    deleteStoredValue(SETTINGS_KEY);
    return readSettings();
  }

  function defaultState() {
    return {
      version: 2,
      running: false,
      controllerId: null,
      activeView: "main",
      pollAttempt: 0,
      lastTarget: null,
      pendingBooking: null,
      submittingBooking: null,
      submitAttempts: {},
      sessionRecoveryAttempts: 0,
      lastKeepAliveAt: 0,
      summary: null,
      logs: [],
    };
  }

  function readState() {
    try {
      const raw = globalThis.sessionStorage?.getItem(STATE_KEY);
      return raw ? { ...defaultState(), ...JSON.parse(raw) } : defaultState();
    } catch (_error) {
      return defaultState();
    }
  }

  function writeState(state) {
    const next = {
      ...defaultState(),
      ...state,
      submitAttempts: { ...(state.submitAttempts ?? {}) },
      logs: Array.isArray(state.logs) ? state.logs : [],
    };
    globalThis.sessionStorage?.setItem(STATE_KEY, JSON.stringify(next));
    return next;
  }

  function patchState(mutator) {
    return writeState(mutator(readState()));
  }

  function resetRuntimeState() {
    const state = readState();
    writeState({
      ...defaultState(),
      activeView: state.activeView,
    });
    renderPanel();
  }

  function setActiveView(view) {
    patchState((state) => ({ ...state, activeView: view }));
    renderPanel();
  }

  function makeControllerId(kind) {
    return `${kind}:${Date.now()}:${Math.random().toString(36).slice(2, 10)}`;
  }

  function isControllerActive(controllerId) {
    const state = readState();
    return Boolean(controllerId && state.running && state.controllerId === controllerId);
  }

  function claimPageController(kind) {
    const state = readState();
    if (!state.running) {
      return null;
    }
    if (activeControllerId && state.controllerId === activeControllerId) {
      appendLog("debug", "Controller is already active; not starting another loop.", {
        controllerId: activeControllerId,
      });
      return null;
    }
    const controllerId = makeControllerId(kind);
    activeControllerId = controllerId;
    patchState((next) => ({ ...next, controllerId }));
    return controllerId;
  }

  function prepareManualControllerStart(kind) {
    const controllerId = makeControllerId(kind);
    activeControllerId = controllerId;
    patchState((next) => ({
      ...next,
      running: true,
      controllerId,
      pollAttempt: 0,
      sessionRecoveryAttempts: 0,
      pendingBooking: null,
      submittingBooking: null,
    }));
    return controllerId;
  }

  function logRank(level) {
    return LOG_LEVELS.indexOf(level);
  }

  function shouldLog(level, settings = readSettings()) {
    return logRank(level) >= logRank(settings.logging.level);
  }

  function appendLog(level, message, detail = "") {
    const settings = readSettings();
    const entry = {
      ts: new Date().toISOString(),
      level,
      message: compactText(message),
      detail: typeof detail === "string" ? detail : JSON.stringify(detail),
    };
    if (shouldLog(level, settings)) {
      const method = level === "error" ? "error" : level === "warn" ? "warn" : "log";
      console[method](`[160Grab ${level}] ${entry.message}`, entry.detail);
    }
    patchState((state) => ({
      ...state,
      logs: [...(state.logs ?? []), entry].slice(-settings.logging.maxEntries),
      summary: entry,
    }));
    return entry;
  }

  function statusClassName(summary) {
    return summary?.level === "error"
      ? "grab160-status-error"
      : summary?.level === "warn"
        ? "grab160-status-warn"
        : "grab160-status-success";
  }

  function panelDoctorText(state = readState()) {
    const target = state.lastTarget ?? parseDoctorPageUrl(location.href) ?? {};
    if (isResolvedTarget(target)) {
      return "resolved";
    }
    if (isCompleteTarget(target) || target.doctorId) {
      return "detected";
    }
    return "unresolved";
  }

  function updatePanelStatus() {
    const panel = document.getElementById(PANEL_ID);
    const body = panel?.querySelector(".grab160-body");
    if (!body) {
      return false;
    }
    const state = readState();
    const settings = readSettings();
    const summary = state.summary;
    const message = body.querySelector("[data-panel-summary-message]");
    if (message) {
      message.classList.remove(
        "grab160-status-error",
        "grab160-status-warn",
        "grab160-status-success",
      );
      message.classList.add(statusClassName(summary));
      message.textContent = summary?.message ?? "Ready";
    }
    const detail = body.querySelector("[data-panel-summary-detail]");
    if (detail) {
      detail.textContent = summary?.detail ?? "";
    }
    const running = body.querySelector("[data-panel-running]");
    if (running) {
      running.textContent = `running=${state.running ? "yes" : "no"} · autoSubmit=${
        settings.booking.autoSubmit ? "ON" : "off"
      } · attempts=${state.pollAttempt}`;
    }
    const target = body.querySelector("[data-panel-target]");
    if (target) {
      target.textContent = `doctor=${panelDoctorText(state)}`;
    }
    const startButton = body.querySelector('[data-action="start"]');
    if (startButton) {
      startButton.textContent = state.running ? "Restart" : "Start";
    }
    return true;
  }

  function refreshPanel({ force = false } = {}) {
    const state = readState();
    const panel = document.getElementById(PANEL_ID);
    if (
      !force &&
      state.activeView === "settings" &&
      panel?.querySelector("[data-settings-view]")
    ) {
      updatePanelStatus();
      return;
    }
    renderPanel();
  }

  function setSummary(level, message, detail = "", options = {}) {
    appendLog(level, message, detail);
    refreshPanel(options);
  }

  function parseDoctorPageUrl(rawUrl) {
    let url;
    try {
      url = new URL(rawUrl, location.origin);
    } catch (_error) {
      return null;
    }
    const fullMatch = url.pathname.match(
      /^\/doctors\/index\/unit_id-([^/]+)\/dep_id-([^/]+)\/docid-([^/.]+)\.html$/,
    );
    if (fullMatch) {
      return {
        unitId: compactText(fullMatch[1]),
        depId: compactText(fullMatch[2]),
        doctorId: compactText(fullMatch[3]),
        sourceUrl: `${url.origin}${url.pathname}`,
        needsResolution: compactText(fullMatch[2]) === "0",
      };
    }
    const docOnlyMatch = url.pathname.match(/^\/doctors\/index\/docid-([^/.]+)\.html$/);
    if (docOnlyMatch) {
      return {
        unitId: null,
        depId: null,
        doctorId: compactText(docOnlyMatch[1]),
        sourceUrl: `${url.origin}${url.pathname}`,
        needsResolution: true,
      };
    }
    return null;
  }

  function parseBookingUrl(rawUrl) {
    let url;
    try {
      url = new URL(rawUrl, location.origin);
    } catch (_error) {
      return null;
    }
    const match = url.pathname.match(
      /^\/guahao\/ystep1\/uid-([^/]+)\/depid-([^/]+)\/schid-([^/.]+)\.html$/,
    );
    if (!match) {
      return null;
    }
    return {
      unitId: compactText(match[1]),
      depId: compactText(match[2]),
      scheduleId: compactText(match[3]),
      sourceUrl: `${url.origin}${url.pathname}`,
    };
  }

  function buildDoctorUrl(target) {
    return `https://www.91160.com/doctors/index/unit_id-${target.unitId}/dep_id-${target.depId}/docid-${target.doctorId}.html`;
  }

  function buildBookingUrl(target, scheduleId) {
    return `https://www.91160.com/guahao/ystep1/uid-${target.unitId}/depid-${target.depId}/schid-${scheduleId}.html`;
  }

  function isCompleteTarget(target) {
    return Boolean(target?.unitId && target?.depId && target?.doctorId);
  }

  function isResolvedTarget(target) {
    return isCompleteTarget(target) && compactText(target.depId) !== "0";
  }

  function areTargetsCompatible(expected, actual) {
    if (!expected || !actual) {
      return true;
    }
    for (const key of ["unitId", "depId", "doctorId"]) {
      if (expected[key] && actual[key] && expected[key] !== actual[key]) {
        return false;
      }
    }
    return true;
  }

  function normalizeTargetConfig(target) {
    const normalized = {
      unitId: normalizeOptionalValue(target?.unitId),
      depId: normalizeOptionalValue(target?.depId),
      doctorId: normalizeOptionalValue(target?.doctorId),
    };
    return normalized.unitId || normalized.depId || normalized.doctorId
      ? normalized
      : null;
  }

  function targetFromAttrs(attrs) {
    if (!attrs) {
      return null;
    }
    const target = {
      unitId: normalizeOptionalValue(attrs.unit_id ?? attrs.unitId ?? attrs["data-unit-id"]),
      depId: normalizeOptionalValue(attrs.dep_id ?? attrs.depId ?? attrs["data-dept-id"]),
      doctorId: normalizeOptionalValue(
        attrs.doctor_id ?? attrs.doctorId ?? attrs.docid ?? attrs.docId,
      ),
    };
    return isCompleteTarget(target) || target.doctorId ? target : null;
  }

  function targetFromScheduleRowId(rowId, unitIdFallback) {
    const match = compactText(rowId).match(/^([^_]+)_([^_]+)_(am|pm|em)$/i);
    if (!match) {
      return null;
    }
    return {
      unitId: normalizeOptionalValue(unitIdFallback),
      depId: normalizeOptionalValue(match[1]),
      doctorId: normalizeOptionalValue(match[2]),
    };
  }

  function snapshotCurrentDoctorPage(doc = document, href = location.href) {
    const addMark = doc.querySelector("#addMark, .focus_btn");
    const collectHrefs = (selector) =>
      Array.from(doc.querySelectorAll(selector))
        .map((element) => compactText(element.getAttribute("href")))
        .filter(Boolean);
    const addMarkAttrs = addMark
      ? Array.from(addMark.attributes).reduce((accumulator, attribute) => {
          accumulator[attribute.name] = attribute.value;
          return accumulator;
        }, {})
      : null;
    return {
      href,
      addMarkAttrs,
      doctorLinks: collectHrefs('a[href*="/doctors/index/unit_id-"][href*="/docid-"]'),
      bookingLinks: collectHrefs('a[href*="/guahao/ystep1/uid-"]'),
      scheduleRowIds: Array.from(doc.querySelectorAll("li.liClassData[id]"))
        .map((element) => compactText(element.id))
        .filter(Boolean),
    };
  }

  function resolveTargetFromSnapshot(snapshot, configuredTarget) {
    const normalizedConfig = normalizeTargetConfig(configuredTarget);
    const candidates = [];
    const urlTarget = parseDoctorPageUrl(snapshot?.href ?? "");
    const attrTarget = targetFromAttrs(snapshot?.addMarkAttrs);
    if (urlTarget) {
      candidates.push({ source: "url", target: urlTarget });
    }
    if (attrTarget) {
      candidates.push({ source: "addMark", target: attrTarget });
    }
    for (const href of snapshot?.doctorLinks ?? []) {
      const target = parseDoctorPageUrl(href);
      if (target) {
        candidates.push({ source: "doctor-link", target });
      }
    }
    for (const href of snapshot?.bookingLinks ?? []) {
      const bookingTarget = parseBookingUrl(href);
      if (bookingTarget) {
        candidates.push({
          source: "booking-link",
          target: {
            unitId: bookingTarget.unitId,
            depId: bookingTarget.depId,
            doctorId:
              normalizedConfig?.doctorId ??
              attrTarget?.doctorId ??
              urlTarget?.doctorId ??
              null,
          },
        });
      }
    }
    const unitIdFallback =
      attrTarget?.unitId ?? normalizedConfig?.unitId ?? urlTarget?.unitId ?? null;
    for (const rowId of snapshot?.scheduleRowIds ?? []) {
      const target = targetFromScheduleRowId(rowId, unitIdFallback);
      if (target) {
        candidates.push({ source: "schedule-row", target });
      }
    }

    const compatible = candidates.filter((candidate) =>
      areTargetsCompatible(normalizedConfig, candidate.target),
    );
    const winner =
      compatible.find((candidate) => isResolvedTarget(candidate.target)) ??
      compatible.find((candidate) => isCompleteTarget(candidate.target)) ??
      compatible[0];
    const merged = {
      unitId: normalizedConfig?.unitId ?? winner?.target?.unitId ?? null,
      depId: normalizedConfig?.depId ?? winner?.target?.depId ?? null,
      doctorId: normalizedConfig?.doctorId ?? winner?.target?.doctorId ?? null,
    };
    if (!merged.doctorId) {
      return { ok: false, reason: "Could not resolve doctor_id from page." };
    }
    if (!merged.unitId || !merged.depId || merged.depId === "0") {
      return { ok: false, reason: "Could not resolve full unit_id/dep_id from page." };
    }
    return { ok: true, target: merged, source: winner?.source ?? "config" };
  }

  function parseTimeToMinutes(value) {
    const match = compactText(value).match(/^(\d{1,2}):(\d{2})$/);
    if (!match) {
      return null;
    }
    const hour = Number(match[1]);
    const minute = Number(match[2]);
    if (hour < 0 || hour > 23 || minute < 0 || minute > 59) {
      return null;
    }
    return hour * 60 + minute;
  }

  function parseTimeRange(value) {
    const text = compactText(value);
    if (!text.includes("-")) {
      return null;
    }
    const [startText, endText] = text.split("-", 2);
    const start = parseTimeToMinutes(startText);
    const end = parseTimeToMinutes(endText);
    return start !== null && end !== null && start < end ? [start, end] : null;
  }

  function rangesOverlap(slotStart, slotEnd, filterRange) {
    return slotStart < filterRange[1] && slotEnd > filterRange[0];
  }

  function slotMatchesHours(slot, hours) {
    if (!hours.length || !compactText(slot.timeRange)) {
      return true;
    }
    const slotRange = parseTimeRange(slot.timeRange);
    if (!slotRange) {
      return false;
    }
    return hours.some((hourFilter) => {
      const filterRange = parseTimeRange(hourFilter);
      return filterRange ? rangesOverlap(slotRange[0], slotRange[1], filterRange) : false;
    });
  }

  function mapPaibanStatus(yState) {
    const value = Number(yState);
    if (value === 1) return "available";
    if (value === 0) return "full";
    if (value === -1) return "expired";
    if (value === -2) return "stopped";
    if (value === -3) return "not_open";
    return "unavailable";
  }

  function walkScheduleTree(node, path = [], output = []) {
    if (!node || typeof node !== "object") {
      return output;
    }
    if (
      Object.prototype.hasOwnProperty.call(node, "schedule_id") &&
      Object.prototype.hasOwnProperty.call(node, "y_state")
    ) {
      output.push([path, node]);
      return output;
    }
    for (const [key, value] of Object.entries(node)) {
      walkScheduleTree(value, path.concat(String(key)), output);
    }
    return output;
  }

  function parseDoctorSchedulePayload(payload, target) {
    const schedules = payload?.data?.schedules;
    if (Array.isArray(schedules) && schedules.length > 0) {
      return schedules.map((item) => ({
        scheduleId: compactText(item.schedule_id),
        doctorId: compactText(item.doctor_id),
        weekday: Number(item.weekday ?? 0),
        dayPeriod: compactText(item.day_period).toLowerCase(),
        hospital: compactText(item.hospital),
        department: compactText(item.department),
        doctor: compactText(item.doctor),
        date: compactText(item.date),
        timeRange: compactText(item.time_range),
        status: compactText(item.status).toLowerCase(),
        unitId: compactText(item.unit_id),
        depId: compactText(item.dep_id),
        docId: compactText(item.doc_id),
      }));
    }
    if (!payload?.sch || !target) {
      return [];
    }
    const weekdayMap = { 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 日: 7 };
    const labels = payload.dates ?? {};
    return walkScheduleTree(payload.sch).map(([path, item]) => {
      const dateKey =
        [...path].reverse().find((part) => /^\d{4}-\d{2}-\d{2}$/.test(part)) ||
        compactText(item.to_date);
      const halfKey =
        [...path].reverse().find((part) => /_(am|pm|em)$/i.test(part)) || "";
      return {
        scheduleId: compactText(item.schedule_id),
        doctorId: compactText(item.doctor_id) || target.doctorId,
        weekday: weekdayMap[compactText(labels[dateKey])] ?? 0,
        dayPeriod:
          compactText(item.day_period).toLowerCase() ||
          compactText(halfKey.split("_").pop()).toLowerCase(),
        hospital: compactText(item.unit_name),
        department: compactText(item.schext_clinic_label || item.dep_name),
        doctor: compactText(item.doctor_name),
        date: compactText(item.to_date) || dateKey,
        timeRange: compactText(item.time_range || item.time_slot || item.time_desc),
        status: mapPaibanStatus(item.y_state),
        unitId: compactText(item.unit_id) || target.unitId,
        depId: compactText(item.dep_id) || target.depId,
        docId: compactText(item.doc_id || item.doctor_id) || target.doctorId,
      };
    });
  }

  function filterSlots(slots, target, filters) {
    return slots.filter((slot) => {
      if (target?.doctorId && compactText(slot.doctorId) !== compactText(target.doctorId)) {
        return false;
      }
      if (filters.weeks.length && !filters.weeks.includes(Number(slot.weekday))) {
        return false;
      }
      if (filters.days.length && !filters.days.includes(compactText(slot.dayPeriod))) {
        return false;
      }
      return slotMatchesHours(slot, filters.hours);
    });
  }

  function isBookableSlot(slot) {
    const status = compactText(slot.status).toLowerCase();
    return !status || ["available", "can_booking", "open", "normal"].includes(status);
  }

  function pickNextSlot(slots) {
    return slots.find(isBookableSlot) ?? null;
  }

  function iterTexts(payload, output = []) {
    if (typeof payload === "string") {
      output.push(payload);
    } else if (Array.isArray(payload)) {
      payload.forEach((item) => iterTexts(item, output));
    } else if (payload && typeof payload === "object") {
      Object.values(payload).forEach((value) => iterTexts(value, output));
    }
    return output;
  }

  function extractRateLimitMessage(payload) {
    for (const text of iterTexts(payload)) {
      const compact = compactText(text);
      if (RATE_LIMIT_PATTERNS.some((pattern) => compact.includes(pattern))) {
        return compact;
      }
    }
    return null;
  }

  function readCookieValue(name, cookieText = document.cookie) {
    const prefix = `${name}=`;
    for (const part of String(cookieText ?? "").split(";")) {
      const trimmed = part.trim();
      if (trimmed.startsWith(prefix)) {
        return trimmed.slice(prefix.length);
      }
    }
    return null;
  }

  function rememberUserKey(userKey, source) {
    const candidate = compactText(userKey);
    if (!candidate) {
      return null;
    }
    lastResolvedUserKey = candidate;
    lastResolvedUserKeySource = source;
    return candidate;
  }

  function resolveCurrentUserKey({ allowCached = true } = {}) {
    const candidates = [
      { source: "page-global", value: globalThis._user_key },
      { source: "unsafe-window", value: globalThis.unsafeWindow?._user_key },
      { source: "access_hash-cookie", value: readCookieValue("access_hash") },
    ];
    for (const candidate of candidates) {
      const value = compactText(candidate.value);
      if (value) {
        rememberUserKey(value, candidate.source);
        return { userKey: value, source: candidate.source };
      }
    }
    if (allowCached) {
      const cached = compactText(lastResolvedUserKey);
      if (cached) {
        return { userKey: cached, source: lastResolvedUserKeySource || "cached" };
      }
    }
    return { userKey: null, source: null };
  }

  function findCurrentUserKey(options) {
    return resolveCurrentUserKey(options).userKey;
  }

  function pickDelayMs(range) {
    const [min, max] = normalizeRange(range, [0, 0]);
    if (max <= min) {
      return min;
    }
    return Math.round(min + Math.random() * (max - min));
  }

  function sleepMs(delayMs) {
    return new Promise((resolve) => setTimeout(resolve, Math.max(0, delayMs)));
  }

  function buildUrlWithParams(url, params) {
    const requestUrl = new URL(url, location.origin);
    for (const [key, value] of Object.entries(params ?? {})) {
      requestUrl.searchParams.set(key, value);
    }
    return requestUrl.toString();
  }

  function parseJsonResponse(text, status, source) {
    try {
      return JSON.parse(text);
    } catch (_error) {
      throw new Error(
        `${source} returned non-JSON status=${status} body=${compactText(text).slice(0, 200)}`,
      );
    }
  }

  async function fetchJsonInsidePage(url, params) {
    const pageWindow = globalThis.unsafeWindow || globalThis;
    let lastError = null;
    if (pageWindow.jQuery?.ajax) {
      try {
        return await new Promise((resolve, reject) => {
          pageWindow.jQuery.ajax({
            url,
            type: "GET",
            data: params,
            dataType: "json",
            timeout: 15000,
            success: resolve,
            error: (xhr, textStatus, errorThrown) => {
              const body = compactText(xhr?.responseText).slice(0, 200);
              reject(
                new Error(
                  `ajax error status=${xhr?.status ?? ""} textStatus=${textStatus ?? ""} error=${errorThrown ?? ""} body=${body}`,
                ),
              );
            },
          });
        });
      } catch (error) {
        lastError = error;
        appendLog("debug", "Page jQuery schedule request failed; trying fetch.", error.message);
      }
    }
    const requestUrl = buildUrlWithParams(url, params);
    const fetchImpl = pageWindow.fetch || globalThis.fetch;
    if (typeof fetchImpl === "function") {
      try {
        const response = await fetchImpl.call(pageWindow, requestUrl, {
          credentials: "omit",
        });
        const text = await response.text();
        return parseJsonResponse(text, response.status, "fetch");
      } catch (error) {
        lastError = error;
        appendLog("debug", "Page fetch schedule request failed.", error.message);
      }
    }
    throw new Error(
      `Page schedule request failed: ${lastError?.message || "no supported page transport"}`,
    );
  }

  async function fetchDoctorSchedule(target, settings) {
    const userKey = findCurrentUserKey();
    if (!userKey) {
      return {
        result_code: 0,
        error_code: "10021",
        error_msg: "请登录后查看医生号源",
      };
    }
    return await fetchJsonInsidePage(
      "https://gate.91160.com/guahao/v1/pc/sch/doctor",
      {
        user_key: userKey,
        docid: target.doctorId,
        doc_id: target.doctorId,
        unit_id: target.unitId,
        dep_id: target.depId,
        date: settings.filters.startDate || new Date().toISOString().slice(0, 10),
        days: "6",
      },
    );
  }

  function appointmentKey(scheduleId, appointmentValue) {
    return `${compactText(scheduleId)}::${compactText(appointmentValue) || "<none>"}`;
  }

  function getSubmitAttempts(scheduleId, appointmentValue) {
    return Number(readState().submitAttempts[appointmentKey(scheduleId, appointmentValue)] ?? 0);
  }

  function recordSubmitAttempt(scheduleId, appointmentValue) {
    const key = appointmentKey(scheduleId, appointmentValue);
    return patchState((state) => ({
      ...state,
      submitAttempts: {
        ...(state.submitAttempts ?? {}),
        [key]: Number(state.submitAttempts?.[key] ?? 0) + 1,
      },
    })).submitAttempts[key];
  }

  function parseAppointmentOptions() {
    const container = document.querySelector("#delts") ?? document;
    return Array.from(container.querySelectorAll("li[val]"))
      .map((element) => ({
        value: compactText(element.getAttribute("val")),
        label: compactText(element.textContent),
        element,
      }))
      .filter((option) => option.value && option.label);
  }

  function chooseAppointmentOption(options, filters) {
    if (!Array.isArray(options) || options.length === 0) {
      return null;
    }
    if (!filters.hours.length) {
      return options[0];
    }
    return (
      options.find((option) => {
        const optionRange = parseTimeRange(option.label);
        if (!optionRange) {
          return false;
        }
        return filters.hours.some((hourFilter) => {
          const filterRange = parseTimeRange(hourFilter);
          return filterRange
            ? rangesOverlap(optionRange[0], optionRange[1], filterRange)
            : false;
        });
      }) ?? null
    );
  }

  function parseBookingFormState(filters, fallbackScheduleId) {
    const scheduleId =
      compactText(document.querySelector('input[name="schedule_id"]')?.value) ||
      compactText(fallbackScheduleId);
    const appointmentOptions = parseAppointmentOptions();
    const appointment = chooseAppointmentOption(appointmentOptions, filters);
    let invalidReason = null;
    if (!scheduleId) {
      invalidReason = "missing_schedule_id";
    } else if (filters.hours.length && !appointment) {
      invalidReason = appointmentOptions.length
        ? "hour_filter_mismatch"
        : "no_appointment_options";
    }
    return {
      scheduleId,
      appointmentValue: appointment?.value ?? null,
      appointmentLabel: appointment?.label ?? null,
      appointmentOptions,
      isValid: invalidReason === null,
      invalidReason,
    };
  }

  function readScheduleDateFromSerializedData(scheduleId) {
    const serialized = compactText(document.querySelector('input[name="sch_data"]')?.value);
    if (!serialized) {
      return null;
    }
    const scheduleNeedle = compactText(scheduleId);
    const searchStart = scheduleNeedle ? serialized.indexOf(scheduleNeedle) : 0;
    const scoped =
      searchStart >= 0 ? serialized.slice(searchStart, searchStart + 1200) : serialized;
    return scoped.match(/s:7:"to_date";s:10:"(\d{4}-\d{2}-\d{2})"/)?.[1] ?? null;
  }

  function readScheduleDateFromPage() {
    const text = compactText(
      document.querySelector("#jzdate")?.parentElement?.textContent ||
        document.querySelector("#suborder")?.textContent ||
        "",
    );
    const match = text.match(/(20\d{2})年(\d{2})月(\d{2})日/);
    return match ? `${match[1]}-${match[2]}-${match[3]}` : null;
  }

  function fillScheduleDate(formState) {
    const input =
      document.querySelector("#sch_date") ?? document.querySelector('input[name="sch_date"]');
    if (!input) {
      return { ok: true, required: false };
    }
    const existing = compactText(input.value);
    if (existing) {
      return { ok: true, required: true, filled: false, source: "existing", value: existing };
    }
    const date =
      readScheduleDateFromSerializedData(formState?.scheduleId) ?? readScheduleDateFromPage();
    if (!date) {
      return { ok: false, required: true, reason: "Could not infer schedule date." };
    }
    input.value = date;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true, required: true, filled: true, source: "schedule", value: date };
  }

  function clickElement(element) {
    if (!element) {
      return false;
    }
    element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    if ("checked" in element) {
      element.checked = true;
      element.setAttribute("checked", "checked");
      element.dispatchEvent(new Event("change", { bubbles: true }));
    }
    return true;
  }

  function isPlaceholderSelectValue(value) {
    const text = compactText(value);
    return !text || text === "0";
  }

  function optionText(option) {
    return compactText(option?.textContent || option?.text || option?.label);
  }

  function canonicalPlaceText(value) {
    return compactText(value)
      .replace(/\s+/g, "")
      .replace(/[省市区县]$/, "");
  }

  function findSelectOption(select, spec) {
    const expected = compactText(spec);
    if (!select || !expected) {
      return null;
    }
    const options = Array.from(select.options ?? []).filter(
      (option) => !isPlaceholderSelectValue(option.value),
    );
    const expectedCanonical = canonicalPlaceText(expected);
    return (
      options.find((option) => compactText(option.value) === expected) ??
      options.find((option) => optionText(option) === expected) ??
      options.find((option) => canonicalPlaceText(optionText(option)) === expectedCanonical) ??
      options.find((option) => {
        const text = optionText(option);
        return text.includes(expected) || expected.includes(text);
      }) ??
      null
    );
  }

  function setSelectOption(select, option) {
    if (!select || !option) {
      return false;
    }
    if (compactText(select.value) === compactText(option.value)) {
      return false;
    }
    Array.from(select.options ?? []).forEach((candidate) => {
      candidate.selected = candidate === option;
    });
    select.value = option.value;
    select.dispatchEvent(new Event("input", { bubbles: true }));
    select.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  function selectAddressOption(select, spec, fieldName) {
    if (!select) {
      return { ok: true, skipped: true, field: fieldName, reason: "missing_select" };
    }
    if (!isPlaceholderSelectValue(select.value)) {
      if (!spec) {
        return {
          ok: true,
          field: fieldName,
          value: compactText(select.value),
          text: optionText(select.options?.[select.selectedIndex]),
          alreadySelected: true,
        };
      }
      const currentOption = Array.from(select.options ?? []).find(
        (option) => compactText(option.value) === compactText(select.value),
      );
      const desiredOption = findSelectOption(select, spec);
      if (!desiredOption) {
        return {
          ok: false,
          field: fieldName,
          reason: `Could not find address ${fieldName} option ${JSON.stringify(spec)}.`,
          availableOptions: Array.from(select.options ?? [])
            .map((candidate) => optionText(candidate))
            .filter(Boolean)
            .slice(0, 20),
        };
      }
      if (currentOption === desiredOption) {
        return {
          ok: true,
          field: fieldName,
          value: compactText(select.value),
          text: optionText(currentOption),
          alreadySelected: true,
        };
      }
      setSelectOption(select, desiredOption);
      return {
        ok: true,
        field: fieldName,
        value: compactText(desiredOption.value),
        text: optionText(desiredOption),
        alreadySelected: false,
      };
    }
    if (!spec) {
      return {
        ok: false,
        field: fieldName,
        reason: `Missing address ${fieldName}; set it in Settings.`,
      };
    }
    const option = findSelectOption(select, spec);
    if (!option) {
      return {
        ok: false,
        field: fieldName,
        reason: `Could not find address ${fieldName} option ${JSON.stringify(spec)}.`,
        availableOptions: Array.from(select.options ?? [])
          .map((candidate) => optionText(candidate))
          .filter(Boolean)
          .slice(0, 20),
      };
    }
    setSelectOption(select, option);
    return {
      ok: true,
      field: fieldName,
      value: compactText(option.value),
      text: optionText(option),
      alreadySelected: false,
    };
  }

  function readMemberAddress(memberSelection) {
    const radio = memberSelection?.radio;
    if (!radio?.getAttribute) {
      return {};
    }
    return {
      province: normalizeOptionalValue(radio.getAttribute("province_id")),
      city: normalizeOptionalValue(radio.getAttribute("city_id")),
      area: normalizeOptionalValue(radio.getAttribute("area_id")),
      detail: normalizeOptionalValue(radio.getAttribute("address")),
    };
  }

  function fillAddressSelection(addressConfig, memberSelection) {
    const provinceSelect = document.querySelector("#useraddress_province");
    const citySelect = document.querySelector("#useraddress_city");
    const areaSelect =
      document.querySelector("#useraddress_area") ??
      document.querySelector('select[name="addressId"]');
    if (!provinceSelect && !citySelect && !areaSelect) {
      return { ok: true, required: false };
    }

    const memberAddress = readMemberAddress(memberSelection);
    const desired = {
      province: memberAddress.province || addressConfig?.province,
      city: memberAddress.city || addressConfig?.city,
      area: memberAddress.area || addressConfig?.area,
      detail: memberAddress.detail || addressConfig?.detail,
    };
    const detailInput =
      document.querySelector("#useraddress_detail") ??
      document.querySelector('input[name="address"]');
    let detailFilled = false;
    if (detailInput && desired.detail && !compactText(detailInput.value)) {
      detailInput.value = desired.detail;
      detailInput.dispatchEvent(new Event("input", { bubbles: true }));
      detailInput.dispatchEvent(new Event("change", { bubbles: true }));
      detailFilled = true;
    }

    const province = selectAddressOption(provinceSelect, desired.province, "province");
    if (!province.ok) {
      return { ok: false, required: true, reason: province.reason, province };
    }
    const city = selectAddressOption(citySelect, desired.city, "city");
    if (!city.ok) {
      return { ok: false, required: true, reason: city.reason, province, city };
    }
    const area = selectAddressOption(areaSelect, desired.area, "area");
    if (!area.ok) {
      return { ok: false, required: true, reason: area.reason, province, city, area };
    }
    if (areaSelect && isPlaceholderSelectValue(areaSelect.value)) {
      return {
        ok: false,
        required: true,
        reason: "Address area is still not selected.",
        province,
        city,
        area,
      };
    }
    return {
      ok: true,
      required: true,
      province,
      city,
      area,
      detailFilled,
    };
  }

  function readMemberClinicId(memberSelection) {
    const radio = memberSelection?.radio;
    if (!radio?.getAttribute) {
      return { value: null, source: null };
    }
    for (const source of ["real_card", "true_value", "card", "social_card"]) {
      const value = normalizeOptionalValue(radio.getAttribute(source));
      if (value) {
        return { value, source: `member-radio:${source}` };
      }
    }
    return { value: null, source: null };
  }

  function fillClinicId(memberSelection) {
    const input =
      document.querySelector("#hismemid") ??
      document.querySelector('input[name="hisMemId"]') ??
      document.querySelector('input[name="hismemid"]') ??
      document.querySelector('select[name="hismemid"]');
    if (!input) {
      return { ok: true, required: false };
    }

    const existing = compactText(input.value);
    if (existing) {
      if (input.getAttribute && !compactText(input.getAttribute("true_value"))) {
        input.setAttribute?.("true_value", existing);
      }
      return {
        ok: true,
        required: true,
        filled: false,
        source: "existing",
        valueLength: existing.length,
      };
    }

    const candidate = readMemberClinicId(memberSelection);
    if (!candidate.value) {
      return {
        ok: true,
        required: true,
        filled: false,
        reason: "No member clinic card/id value was available.",
      };
    }

    input.value = candidate.value;
    input.setAttribute?.("true_value", candidate.value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    input.dispatchEvent(new Event("blur", { bubbles: true }));
    return {
      ok: true,
      required: true,
      filled: true,
      source: candidate.source,
      valueLength: candidate.value.length,
    };
  }

  function memberRadioLabelText(radio) {
    const container = radio.closest("tr, li, label, .patient_item, .member_item, .person_item");
    return compactText(container?.textContent || radio.parentElement?.textContent);
  }

  function isLikelyMemberRadio(radio) {
    const name = compactText(radio.getAttribute("name")).toLowerCase();
    if (["mid", "member_id", "memberid", "his_mem_id"].includes(name)) {
      return true;
    }
    if (
      compactText(radio.getAttribute("data-member-id")) ||
      compactText(radio.getAttribute("data-mid"))
    ) {
      return true;
    }
    return Boolean(
      radio.closest(
        'tr[id^="mem"], [data-member-id], [data-mid], .member_item, .patient_item, .person_item',
      ),
    );
  }

  function collectRadioGroups() {
    const radios = Array.from(document.querySelectorAll('input[type="radio"]'));
    return {
      radios,
      memberRadios: radios.filter(isLikelyMemberRadio),
      ignoredRadios: radios.filter((radio) => !isLikelyMemberRadio(radio)),
    };
  }

  function memberRadioDebugRows(radios) {
    return radios.map((radio, index) => ({
      index,
      value: compactText(radio.value),
      name: radio.getAttribute("name") ?? "",
      id: radio.getAttribute("id") ?? "",
      checked: Boolean(radio.checked),
      disabled: Boolean(radio.disabled),
      label: memberRadioLabelText(radio),
    }));
  }

  function memberRadioDebugSummary(memberRadios, ignoredRadios = []) {
    const lines = [
      `Found ${memberRadios.length} candidate member radio(s) out of ${
        memberRadios.length + ignoredRadios.length
      } total <input type="radio"> element(s).`,
      ...memberRadioDebugRows(memberRadios).map(
        (row) =>
          `member#${row.index}\tvalue=${JSON.stringify(row.value)}\tname=${JSON.stringify(row.name)}\tlabel=${JSON.stringify(row.label)}`,
      ),
    ];
    if (ignoredRadios.length) {
      lines.push(
        "",
        `Ignored ${ignoredRadios.length} non-member radio(s).`,
        ...memberRadioDebugRows(ignoredRadios).map(
          (row) =>
            `ignored#${row.index}\tvalue=${JSON.stringify(row.value)}\tname=${JSON.stringify(row.name)}\tlabel=${JSON.stringify(row.label)}`,
        ),
      );
    }
    return lines.join("\n");
  }

  function resolveMemberSelection(memberConfig) {
    const { memberRadios, ignoredRadios } = collectRadioGroups();
    const hiddenMemberId =
      compactText(document.querySelector('input[name="member_id"]')?.value) ||
      compactText(document.querySelector('input[name="mid"]')?.value) ||
      compactText(document.querySelector("#member_id")?.value);
    if (memberConfig.memberId) {
      const radio = memberRadios.find(
        (candidate) =>
          compactText(candidate.value) === memberConfig.memberId ||
          compactText(candidate.getAttribute("data-member-id")) === memberConfig.memberId ||
          compactText(candidate.getAttribute("data-mid")) === memberConfig.memberId,
      );
      if (!radio && hiddenMemberId === memberConfig.memberId) {
        return { ok: true, memberId: memberConfig.memberId, radio: null };
      }
      if (!radio && (memberRadios.length || ignoredRadios.length)) {
        return {
          ok: false,
          reason: [
            `Configured memberId ${memberConfig.memberId} was not found.`,
            memberRadioDebugSummary(memberRadios, ignoredRadios),
          ].join("\n\n"),
        };
      }
      return { ok: true, memberId: memberConfig.memberId, radio: radio ?? null };
    }
    if (memberConfig.memberLabel) {
      const radio = memberRadios.find((candidate) =>
        memberRadioLabelText(candidate).includes(memberConfig.memberLabel),
      );
      if (!radio) {
        return {
          ok: false,
          reason: [
            `Configured memberLabel ${memberConfig.memberLabel} was not found.`,
            memberRadioDebugSummary(memberRadios, ignoredRadios),
          ].join("\n\n"),
        };
      }
      return { ok: true, memberId: compactText(radio.value), radio };
    }
    if (memberRadios.length === 1) {
      return {
        ok: true,
        memberId: compactText(memberRadios[0].value),
        radio: memberRadios[0],
      };
    }
    if (memberRadios.length > 1) {
      return {
        ok: false,
        reason: [
          "Multiple member candidates were found. Set memberId or memberLabel first.",
          memberRadioDebugSummary(memberRadios, ignoredRadios),
        ].join("\n\n"),
      };
    }
    if (hiddenMemberId) {
      return { ok: true, memberId: hiddenMemberId, radio: null };
    }
    return { ok: false, reason: "No member selection was found on this booking page." };
  }

  function fillBookingForm(
    formState,
    memberSelection,
    addressConfig,
    bookingConfig = CONFIG_DEFAULTS.booking,
  ) {
    if (formState.appointmentValue) {
      const appointmentElement = formState.appointmentOptions.find(
        (option) => option.value === formState.appointmentValue,
      )?.element;
      clickElement(appointmentElement);
    }
    for (const selector of [
      'input[name="member_id"]',
      "#member_id",
      'input[name="memberId"]',
      "#memberId",
      'input[name="mid"]',
      "#mid",
      'input[name="his_mem_id"]',
      "#his_mem_id",
    ]) {
      const input = document.querySelector(selector);
      if (input) {
        input.value = memberSelection.memberId;
      }
    }
    if (memberSelection.radio) {
      clickElement(memberSelection.radio);
    }
    const clinicIdSelection = fillClinicId(memberSelection);
    const addressSelection = fillAddressSelection(addressConfig, memberSelection);
    const scheduleDateSelection = fillScheduleDate(formState);
    const diseaseDescription =
      normalizeOptionalValue(bookingConfig?.diseaseDescription) ?? DEFAULT_DISEASE_DESCRIPTION;
    for (const selector of [
      'input[name="disease_input"]',
      "#disease_input",
      'textarea[name="disease_content"]',
      "#disease_content",
    ]) {
      const input = document.querySelector(selector);
      if (input && !compactText(input.value)) {
        input.value = diseaseDescription;
      }
    }
    for (const selector of ['input[name="accept"][value="1"]', "#check_yuyue_rule"]) {
      const input = document.querySelector(selector);
      if (input) {
        input.checked = true;
        input.setAttribute("checked", "checked");
      }
    }
    return { addressSelection, clinicIdSelection, scheduleDateSelection };
  }

  function isCheckIdInfoUrl(url) {
    return /checkidinfo|checkIdInfo/.test(String(url || ""));
  }

  function normalizeCheckIdInfoJsonResponse(data, dataType) {
    if (
      typeof data === "string" &&
      data.trim() === "" &&
      compactText(dataType).toLowerCase().includes("json")
    ) {
      return "{}";
    }
    return data;
  }

  function installCheckIdInfoBlankResponsePatch() {
    const pageWindow = globalThis.unsafeWindow || globalThis;
    const jquery = pageWindow.jQuery || pageWindow.$;
    if (!jquery || typeof jquery.ajax !== "function") {
      return { installed: false, reason: "page-jquery-unavailable" };
    }
    if (jquery.__grab160CheckIdInfoBlankResponsePatch) {
      return { installed: false, alreadyInstalled: true };
    }

    const originalAjax = jquery.ajax;
    const patchedAjax = function grab160PatchedAjax(...args) {
      const options = args[0] && typeof args[0] === "object" ? args[0] : args[1];
      const url = typeof args[0] === "string" ? args[0] : options?.url;
      if (!options || typeof options !== "object" || !isCheckIdInfoUrl(url)) {
        return originalAjax.apply(this, args);
      }

      const nextOptions = { ...options };
      const originalDataFilter = nextOptions.dataFilter;
      nextOptions.dataFilter = function grab160CheckIdInfoDataFilter(data, dataType) {
        const filtered =
          typeof originalDataFilter === "function"
            ? originalDataFilter.call(this, data, dataType)
            : data;
        return normalizeCheckIdInfoJsonResponse(
          filtered,
          nextOptions.dataType || dataType,
        );
      };

      if (typeof args[0] === "string") {
        return originalAjax.call(this, args[0], nextOptions);
      }
      return originalAjax.call(this, nextOptions);
    };
    patchedAjax.__grab160OriginalAjax = originalAjax;
    jquery.ajax = patchedAjax;
    jquery.__grab160CheckIdInfoBlankResponsePatch = true;
    return { installed: true };
  }

  function isVisible(element) {
    if (!element) {
      return false;
    }
    const style = globalThis.getComputedStyle(element);
    return style.display !== "none" && style.visibility !== "hidden";
  }

  function findSubmitControl() {
    for (const selector of [
      "#suborder #submitbtn",
      "#submitbtn",
      "#submit_booking",
      "#submitBooking",
      "#sub",
      "#submit",
      '#suborder button[type="submit"]',
      '#suborder input[type="submit"]',
      'button[type="submit"]',
      'input[type="submit"]',
      "button.btn_submit",
      "button.sub-btn",
      "input.sub-btn",
    ]) {
      const element = document.querySelector(selector);
      if (isVisible(element)) {
        return { method: "selector", target: selector, element };
      }
    }
    const textCandidates = Array.from(
      document.querySelectorAll('button, input[type="button"], input[type="submit"], a'),
    ).filter((element) => {
      const text = compactText(element.textContent || element.value);
      return isVisible(element) && /确认预约|提交预约|提交|预约|下一步/.test(text);
    });
    if (textCandidates.length > 0) {
      return {
        method: "text-match",
        target: compactText(textCandidates[0].textContent || textCandidates[0].value),
        element: textCandidates[0],
      };
    }
    const form = document.querySelector("form");
    if (form) {
      if (typeof form.requestSubmit === "function") {
        return { method: "requestSubmit", target: "form", form };
      }
      return { method: "submit", target: "form", form };
    }
    return { method: "not-found", target: null };
  }

  function triggerSubmitControl(control = findSubmitControl()) {
    if (control.method === "not-found") {
      return { method: "not-found", target: null };
    }
    if (control.method === "requestSubmit") {
      control.form.requestSubmit();
      return { method: "requestSubmit", target: control.target };
    }
    if (control.method === "submit") {
      control.form.submit();
      return { method: "submit", target: control.target };
    }
    clickElement(control.element);
    return { method: control.method, target: control.target };
  }

  function markSubmitInProgress(formState, memberSelection, fillResult, attemptCount) {
    const detail = {
      scheduleId: formState.scheduleId,
      appointmentValue: formState.appointmentValue,
      appointmentLabel: formState.appointmentLabel,
      memberId: memberSelection.memberId,
      address: fillResult.addressSelection,
      clinicId: fillResult.clinicIdSelection,
      scheduleDate: fillResult.scheduleDateSelection,
      checkIdInfoPatch: fillResult.checkIdInfoPatch,
      attemptCount,
      startedAt: new Date().toISOString(),
    };
    activeControllerId = null;
    patchState((next) => ({
      ...next,
      running: false,
      controllerId: null,
      pendingBooking: null,
      submittingBooking: detail,
    }));
    setSummary(
      "info",
      "Submitted booking form; runner paused to avoid duplicate submit.",
      detail,
      { force: true },
    );
    return detail;
  }

  async function clickFollowupControl() {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const sure = document.querySelector("#sure");
      if (isVisible(sure)) {
        clickElement(sure);
        return "paymethod-sure";
      }
      const okButton = document.querySelector("#ok_btn");
      if (isVisible(okButton)) {
        clickElement(okButton);
        return "disease-ok";
      }
      await sleepMs(300);
    }
    return null;
  }

  function visibleMessagesSnapshot() {
    return Array.from(
      document.querySelectorAll(
        ".wrong,.warning,.import,.fine,.tips,.msg,.message,.error,.err,.layui-layer-content,.select-member-close,.select-vertifycode-close,.tip,.order-tit",
      ),
    )
      .map((element) => compactText(element.textContent))
      .filter(Boolean);
  }

  function inspectBookingPage(beforeUrl) {
    const currentUrl = location.href;
    const hasBookingForm = Boolean(document.querySelector("#suborder, form"));
    const hasSubmitButton = Boolean(
      document.querySelector(
        "#suborder #submitbtn, #suborder input[type='submit'], #suborder button[type='submit'], #submit_booking",
      ),
    );
    const visibleMessages = visibleMessagesSnapshot();
    return {
      success:
        currentUrl !== beforeUrl && !currentUrl.includes("/guahao/ystep1/") ||
        (!hasBookingForm && !hasSubmitButton),
      currentUrl,
      hasBookingForm,
      hasSubmitButton,
      visibleMessages,
      rateLimitMessage: extractRateLimitMessage(visibleMessages),
    };
  }

  function isLoginExpiredPage() {
    const text = compactText(document.body?.innerText ?? "");
    return (
      location.href.includes("/login.html") ||
      text.includes("请登录") ||
      text.includes("登录后查看")
    );
  }

  function navigateToDoctorPage(target) {
    if (!isCompleteTarget(target)) {
      setSummary("error", "Cannot return to doctor page; target is incomplete.", target);
      patchState((state) => ({ ...state, running: false }));
      return;
    }
    location.replace(buildDoctorUrl(target));
  }

  function startRun() {
    const controllerId = prepareManualControllerStart("doctor");
    if (!controllerId) {
      return;
    }
    setSummary("info", "Started.", "");
    bootstrapController(controllerId);
  }

  function stopRun(reason = "Stopped.") {
    activeControllerId = null;
    patchState((state) => ({
      ...state,
      running: false,
      controllerId: null,
      pendingBooking: null,
      submittingBooking: null,
    }));
    setSummary("info", reason, "");
  }

  function isStartAtReady(settings) {
    if (!settings.runtime.startAt) {
      return true;
    }
    return Date.now() >= Date.parse(settings.runtime.startAt);
  }

  async function waitUntilStartAt(settings, controllerId) {
    while (isControllerActive(controllerId) && !isStartAtReady(settings)) {
      const remaining = Math.max(0, Date.parse(settings.runtime.startAt) - Date.now());
      setSummary("info", "Waiting for configured start time.", `${Math.ceil(remaining / 1000)}s`);
      await sleepMs(Math.min(5000, remaining || 1000));
    }
  }

  async function recoverSession(target, reason, settings) {
    if (!settings.session.recoveryEnabled) {
      stopRun(`Session recovery disabled: ${reason}`);
      return false;
    }
    const state = readState();
    if (state.sessionRecoveryAttempts >= settings.session.recoveryMaxAttempts) {
      stopRun(`Login expired, manual login required: ${reason}`);
      return false;
    }
    const delayMs = pickDelayMs(settings.session.recoveryCooldownMs);
    patchState((next) => ({
      ...next,
      running: true,
      pendingBooking: null,
      submittingBooking: null,
      sessionRecoveryAttempts: next.sessionRecoveryAttempts + 1,
    }));
    setSummary(
      "warn",
      "Session looks expired. Refreshing doctor page before retrying.",
      `${reason}; attempt ${state.sessionRecoveryAttempts + 1}; delay ${delayMs} ms`,
    );
    await sleepMs(delayMs);
    navigateToDoctorPage(target);
    return true;
  }

  async function runDoctorPageController(controllerId) {
    const settings = readSettings();
    const state = readState();
    if (!isControllerActive(controllerId)) {
      if (!state.running) {
        setSummary("info", "Ready. Press Start to poll this doctor page.", "");
      }
      return;
    }
    if (!state.running) {
      setSummary("info", "Ready. Press Start to poll this doctor page.", "");
      return;
    }

    const resolved = resolveTargetFromSnapshot(
      snapshotCurrentDoctorPage(),
      settings.target,
    );
    if (!resolved.ok) {
      stopRun(`Doctor target resolution failed: ${resolved.reason}`);
      return;
    }
    const target = resolved.target;
    patchState((next) => ({ ...next, lastTarget: target }));

    const canonicalUrl = buildDoctorUrl(target);
    if (canonicalUrl !== `${location.origin}${location.pathname}`) {
      setSummary("info", "Redirecting to canonical doctor page.", canonicalUrl);
      location.replace(canonicalUrl);
      return;
    }

    await waitUntilStartAt(settings, controllerId);

    while (isControllerActive(controllerId)) {
      if (!findCurrentUserKey()) {
        await recoverSession(target, "missing _user_key/access_hash", readSettings());
        return;
      }

      const activeSettings = readSettings();
      let payload;
      try {
        payload = await fetchDoctorSchedule(target, activeSettings);
      } catch (error) {
        if (!isControllerActive(controllerId)) {
          return;
        }
        setSummary("warn", "Schedule polling request failed.", error.message);
        await sleepMs(pickDelayMs(activeSettings.pacing.pollMs));
        continue;
      }
      if (!isControllerActive(controllerId)) {
        return;
      }

      if (
        String(payload?.error_code ?? "") === "10021" ||
        compactText(payload?.error_msg).includes("请登录后查看医生号源")
      ) {
        await recoverSession(target, compactText(payload?.error_msg), activeSettings);
        return;
      }

      patchState((next) => ({
        ...next,
        pollAttempt: next.pollAttempt + 1,
        sessionRecoveryAttempts: 0,
        lastKeepAliveAt: Date.now(),
      }));

      const rateLimitMessage = extractRateLimitMessage(payload);
      if (rateLimitMessage) {
        const cooldownMs = pickDelayMs(activeSettings.pacing.rateLimitCooldownMs);
        setSummary("warn", "Schedule polling hit rate limiting.", `${cooldownMs} ms`);
        await sleepMs(cooldownMs);
        continue;
      }

      const slots = filterSlots(
        parseDoctorSchedulePayload(payload, target),
        target,
        activeSettings.filters,
      );
      const nextSlot = pickNextSlot(slots);
      if (nextSlot) {
        patchState((next) => ({
          ...next,
          submittingBooking: null,
          pendingBooking: {
            unitId: target.unitId,
            depId: target.depId,
            doctorId: target.doctorId,
            scheduleId: nextSlot.scheduleId,
          },
        }));
        setSummary(
          "info",
          `Matched slot ${nextSlot.scheduleId}; opening booking page.`,
          compactText(`${nextSlot.date} ${nextSlot.dayPeriod} ${nextSlot.timeRange}`),
        );
        await sleepMs(pickDelayMs(activeSettings.pacing.pageActionMs));
        location.replace(buildBookingUrl(target, nextSlot.scheduleId));
        return;
      }

      setSummary("debug", "No bookable matching slot yet.", {
        target,
        filters: activeSettings.filters,
      });
      refreshPanel();
      await sleepMs(pickDelayMs(activeSettings.pacing.pollMs));
    }
  }

  async function returnToDoctorAfterCurrentAttempt(target, message, delayConfig) {
    const delayMs = pickDelayMs(delayConfig);
    setSummary("warn", message, `Returning to doctor page in ${delayMs} ms.`);
    await sleepMs(delayMs);
    navigateToDoctorPage(target);
  }

  async function runBookingPageController(controllerId) {
    const settings = readSettings();
    const bookingTarget = parseBookingUrl(location.href);
    const state = readState();
    if (!bookingTarget) {
      stopRun("Unsupported booking page URL.");
      return;
    }
    const target = {
      unitId: bookingTarget.unitId,
      depId: bookingTarget.depId,
      doctorId:
        normalizeOptionalValue(state.pendingBooking?.doctorId) ??
        normalizeOptionalValue(state.lastTarget?.doctorId) ??
        normalizeOptionalValue(settings.target.doctorId),
    };
    if (!isControllerActive(controllerId)) {
      if (!state.running) {
        setSummary("info", "Booking page loaded while runner is stopped.", "");
      }
      return;
    }
    if (!state.running) {
      setSummary("info", "Booking page loaded while runner is stopped.", "");
      return;
    }
    if (isLoginExpiredPage()) {
      await recoverSession(target, "booking page requires login", settings);
      return;
    }

    const rateLimitOnLoad = extractRateLimitMessage([
      document.body?.innerText ?? "",
      document.documentElement?.outerHTML ?? "",
    ]);
    if (rateLimitOnLoad) {
      await returnToDoctorAfterCurrentAttempt(
        target,
        `Booking page hit rate limiting: ${rateLimitOnLoad}`,
        settings.pacing.rateLimitCooldownMs,
      );
      return;
    }

    const memberSelection = resolveMemberSelection(settings.member);
    if (!memberSelection.ok) {
      stopRun(`Member selection failed: ${memberSelection.reason}`);
      return;
    }

    const formState = parseBookingFormState(settings.filters, bookingTarget.scheduleId);
    if (!formState.isValid) {
      await returnToDoctorAfterCurrentAttempt(
        target,
        `Booking form invalid: ${formState.invalidReason}`,
        settings.pacing.bookingRetryMs,
      );
      return;
    }

    const attempts = getSubmitAttempts(formState.scheduleId, formState.appointmentValue);
    if (attempts >= settings.booking.maxSubmitAttemptsPerAppointment) {
      stopRun(
        `Submit attempts exhausted for ${formState.scheduleId}/${formState.appointmentValue}.`,
      );
      return;
    }

    const fillResult = fillBookingForm(
      formState,
      memberSelection,
      settings.address,
      settings.booking,
    );
    if (!fillResult.addressSelection.ok) {
      stopRun(`Address selection failed: ${fillResult.addressSelection.reason}`);
      return;
    }
    if (!fillResult.scheduleDateSelection.ok) {
      stopRun(`Schedule date fill failed: ${fillResult.scheduleDateSelection.reason}`);
      return;
    }
    const checkIdInfoPatch = installCheckIdInfoBlankResponsePatch();
    if (checkIdInfoPatch.installed) {
      appendLog("debug", "Installed checkIdInfo blank-response JSON patch.");
    }
    if (!settings.booking.autoSubmit) {
      patchState((next) => ({
        ...next,
        running: false,
        pendingBooking: null,
        submittingBooking: null,
      }));
      setSummary(
        "info",
        "Booking form prepared; waiting for manual submit.",
        {
          scheduleId: formState.scheduleId,
          appointmentValue: formState.appointmentValue,
          appointmentLabel: formState.appointmentLabel,
          memberId: memberSelection.memberId,
          address: fillResult.addressSelection,
          clinicId: fillResult.clinicIdSelection,
          scheduleDate: fillResult.scheduleDateSelection,
          checkIdInfoPatch,
        },
      );
      renderPanel();
      return;
    }

    await sleepMs(pickDelayMs(settings.pacing.pageActionMs));
    const beforeUrl = location.href;
    const submitControl = findSubmitControl();
    if (submitControl.method === "not-found") {
      stopRun("Could not find a submit control on the booking page.");
      return;
    }
    const attemptCount = recordSubmitAttempt(
      formState.scheduleId,
      formState.appointmentValue,
    );
    markSubmitInProgress(
      formState,
      memberSelection,
      { ...fillResult, checkIdInfoPatch },
      attemptCount,
    );
    const submitResult = triggerSubmitControl(submitControl);
    setSummary("info", "Submitted booking form.", { submitResult, attemptCount });
    const followupAction = await clickFollowupControl();
    if (followupAction) {
      appendLog("info", `Triggered follow-up action ${followupAction}.`);
    }

    let inspection = inspectBookingPage(beforeUrl);
    for (const checkpoint of [400, 900, 1600, 2400]) {
      if (inspection.success || inspection.rateLimitMessage) {
        break;
      }
      await sleepMs(checkpoint);
      inspection = inspectBookingPage(beforeUrl);
    }

    if (inspection.success) {
      patchState((next) => ({
        ...next,
        running: false,
        pendingBooking: null,
        submittingBooking: null,
      }));
      setSummary("info", `Booking succeeded for schedule ${formState.scheduleId}.`, {
        appointmentLabel: formState.appointmentLabel,
        url: inspection.currentUrl,
      });
      renderPanel();
      return;
    }

    const reason =
      inspection.rateLimitMessage ||
      inspection.visibleMessages.join(" | ") ||
      `Booking page stayed on ${inspection.currentUrl}`;
    if (!settings.booking.autoReturnAfterSubmitFailure) {
      patchState((next) => ({ ...next, running: false }));
      setSummary("warn", "Booking submit failed; staying on page.", reason);
      renderPanel();
      return;
    }
    patchState((next) => ({ ...next, running: true, submittingBooking: null }));
    await returnToDoctorAfterCurrentAttempt(
      target,
      `Booking submit failed: ${reason}`,
      inspection.rateLimitMessage
        ? settings.pacing.rateLimitCooldownMs
        : settings.pacing.bookingRetryMs,
    );
  }

  function normalizePanelPosition(position) {
    const left = Number(position?.left);
    const top = Number(position?.top);
    return Number.isFinite(left) && Number.isFinite(top)
      ? { left: Math.round(left), top: Math.round(top) }
      : null;
  }

  function readPanelPosition() {
    try {
      return normalizePanelPosition(
        JSON.parse(globalThis.localStorage?.getItem(PANEL_POSITION_KEY) ?? "null"),
      );
    } catch (_error) {
      return null;
    }
  }

  function writePanelPosition(position) {
    const normalized = normalizePanelPosition(position);
    if (normalized) {
      globalThis.localStorage?.setItem(PANEL_POSITION_KEY, JSON.stringify(normalized));
    }
  }

  function applyPanelPosition(panel, position) {
    const normalized = normalizePanelPosition(position);
    if (!normalized) {
      panel.style.top = "16px";
      panel.style.right = "16px";
      panel.style.left = "auto";
      return;
    }
    panel.style.left = `${Math.max(8, normalized.left)}px`;
    panel.style.top = `${Math.max(8, normalized.top)}px`;
    panel.style.right = "auto";
  }

  function installPanelDragging(panel, title) {
    if (!panel || !title || title.dataset.dragInstalled === "1") {
      return;
    }
    title.dataset.dragInstalled = "1";
    title.style.cursor = "move";
    let drag = null;
    title.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      const rect = panel.getBoundingClientRect();
      drag = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        left: rect.left,
        top: rect.top,
      };
      title.setPointerCapture?.(event.pointerId);
      event.preventDefault();
    });
    title.addEventListener("pointermove", (event) => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      applyPanelPosition(panel, {
        left: drag.left + event.clientX - drag.startX,
        top: drag.top + event.clientY - drag.startY,
      });
      event.preventDefault();
    });
    const finish = () => {
      if (!drag) return;
      const rect = panel.getBoundingClientRect();
      writePanelPosition({ left: rect.left, top: rect.top });
      drag = null;
    };
    title.addEventListener("pointerup", finish);
    title.addEventListener("pointercancel", finish);
    title.addEventListener("lostpointercapture", finish);
  }

  function createPanel() {
    let panel = document.getElementById(PANEL_ID);
    if (panel) {
      return panel;
    }
    panel = document.createElement("div");
    panel.id = PANEL_ID;
    panel.innerHTML = `
      <div class="grab160-title">160Grab v${SCRIPT_VERSION}</div>
      <div class="grab160-body"></div>
    `;
    Object.assign(panel.style, {
      position: "fixed",
      zIndex: "999999",
      width: "360px",
      maxHeight: "82vh",
      overflow: "auto",
      padding: "12px",
      borderRadius: "8px",
      boxShadow: "0 8px 28px rgba(0,0,0,0.18)",
      background: "rgba(17,24,39,0.94)",
      color: "#f3f4f6",
      fontSize: "13px",
      lineHeight: "1.45",
      fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, sans-serif",
    });
    const style = document.createElement("style");
    style.textContent = `
      #${PANEL_ID}, #${PANEL_ID} * {
        box-sizing: border-box;
      }
      #${PANEL_ID} button, #${PANEL_ID} input, #${PANEL_ID} select {
        font: inherit;
      }
      #${PANEL_ID} button {
        appearance: none;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border: 1px solid rgba(255,255,255,.18);
        background: rgba(255,255,255,.08);
        color: #f9fafb;
        border-radius: 6px;
        padding: 5px 8px;
        cursor: pointer;
      }
      #${PANEL_ID} button:hover { background: rgba(255,255,255,.16); }
      #${PANEL_ID} input, #${PANEL_ID} select {
        width: 100%;
        box-sizing: border-box;
        border: 1px solid rgba(255,255,255,.18);
        border-radius: 6px;
        padding: 5px 7px;
        background: rgba(255,255,255,.1);
        color: #f9fafb;
      }
      #${PANEL_ID} label { display: block; margin-top: 8px; color: #d1d5db; }
      #${PANEL_ID} .grab160-title { font-weight: 700; margin-bottom: 8px; user-select: none; }
      #${PANEL_ID} .grab160-row { display: flex; gap: 6px; align-items: center; }
      #${PANEL_ID} .grab160-row > * { flex: 1; min-width: 0; }
      #${PANEL_ID} .grab160-buttons { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
      #${PANEL_ID} .grab160-muted { color: #9ca3af; }
      #${PANEL_ID} .grab160-status {
        display: inline;
        position: static;
        width: auto;
        height: auto;
        padding: 0;
        margin: 0;
        line-height: inherit;
        pointer-events: none;
      }
      #${PANEL_ID} .grab160-status-warn { color: #fcd34d; }
      #${PANEL_ID} .grab160-status-error { color: #fca5a5; }
      #${PANEL_ID} .grab160-status-success { color: #86efac; }
      #${PANEL_ID} .grab160-log { white-space: pre-wrap; border-top: 1px solid rgba(255,255,255,.12); padding-top: 6px; margin-top: 6px; }
    `;
    document.documentElement.appendChild(style);
    document.documentElement.appendChild(panel);
    applyPanelPosition(panel, readPanelPosition());
    installPanelDragging(panel, panel.querySelector(".grab160-title"));
    return panel;
  }

  function htmlEscape(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderPanel() {
    const panel = createPanel();
    const body = panel.querySelector(".grab160-body");
    const state = readState();
    const settings = readSettings();
    const summary = state.summary;
    const autoSubmitText = settings.booking.autoSubmit ? "ON" : "off";
    body.innerHTML = `
      <div class="grab160-buttons">
        <button type="button" data-action="start">${state.running ? "Restart" : "Start"}</button>
        <button type="button" data-action="stop">Stop</button>
        <button type="button" data-action="settings">Settings</button>
        <button type="button" data-action="logs">Logs</button>
        <button type="button" data-action="reset-state">Reset State</button>
      </div>
      <div>Status: <span class="grab160-status ${statusClassName(summary)}" data-panel-summary-message>${htmlEscape(summary?.message ?? "Ready")}</span></div>
      <div class="grab160-muted" data-panel-summary-detail>${htmlEscape(summary?.detail ?? "")}</div>
      <div class="grab160-muted" data-panel-running>running=${state.running ? "yes" : "no"} · autoSubmit=${autoSubmitText} · attempts=${state.pollAttempt}</div>
      <div class="grab160-muted" data-panel-target>doctor=${htmlEscape(panelDoctorText(state))}</div>
      ${state.activeView === "settings" ? renderSettingsView(settings) : ""}
      ${state.activeView === "logs" ? renderLogsView(state, settings) : ""}
    `;
    body.querySelector('[data-action="start"]')?.addEventListener("click", startRun);
    body.querySelector('[data-action="stop"]')?.addEventListener("click", () => stopRun());
    body
      .querySelector('[data-action="settings"]')
      ?.addEventListener("click", () => setActiveView("settings"));
    body.querySelector('[data-action="logs"]')?.addEventListener("click", () => setActiveView("logs"));
    body
      .querySelector('[data-action="reset-state"]')
      ?.addEventListener("click", resetRuntimeState);
    if (state.activeView === "settings") {
      wireSettingsView(body, settings);
    }
  }

  function renderSettingsView(settings) {
    const hourRows =
      settings.filters.hours.length > 0 ? settings.filters.hours : ["08:00-09:00"];
    return `
      <div class="grab160-log" data-settings-view>
        <label>Member ID <input data-setting="member.memberId" type="password" value="${htmlEscape(settings.member.memberId ?? "")}"></label>
        <label><input data-setting="member.show" type="checkbox" style="width:auto"> Show member ID</label>
        <label>Member Label <input data-setting="member.memberLabel" value="${htmlEscape(settings.member.memberLabel ?? "")}"></label>
        <div class="grab160-row">
          <label>Province <input data-setting="address.province" value="${htmlEscape(settings.address.province ?? "")}"></label>
          <label>City <input data-setting="address.city" value="${htmlEscape(settings.address.city ?? "")}"></label>
          <label>Area <input data-setting="address.area" value="${htmlEscape(settings.address.area ?? "")}"></label>
        </div>
        <label>Address Detail <input data-setting="address.detail" value="${htmlEscape(settings.address.detail ?? "")}"></label>
        <label>Start At <input data-setting="runtime.startAt" type="datetime-local" value="${htmlEscape(settings.runtime.startAt ?? "")}"></label>
        <label>Appointment From <input data-setting="filters.startDate" type="date" value="${htmlEscape(settings.filters.startDate ?? "")}"></label>
        <div class="grab160-row">${[1, 2, 3, 4, 5, 6, 7]
          .map(
            (day) =>
              `<label><input data-week="${day}" type="checkbox" style="width:auto" ${
                settings.filters.weeks.includes(day) ? "checked" : ""
              }> 周${"一二三四五六日"[day - 1]}</label>`,
          )
          .join("")}</div>
        <div class="grab160-row">${["am", "pm", "em"]
          .map(
            (day) =>
              `<label><input data-period="${day}" type="checkbox" style="width:auto" ${
                settings.filters.days.includes(day) ? "checked" : ""
              }> ${day}</label>`,
          )
          .join("")}</div>
        <label>Hours</label>
        <div data-hours>${hourRows
          .map((range) => {
            const [start, end] = range.split("-");
            return `<div class="grab160-row" data-hour-row><input type="time" step="1800" value="${htmlEscape(start)}"><input type="time" step="1800" value="${htmlEscape(end)}"><button type="button" data-remove-hour>Delete</button></div>`;
          })
          .join("")}</div>
        <button type="button" data-add-hour>Add Time Range</button>
        <div class="grab160-row">
          ${renderRangeInputs("Poll", "pacing.pollMs", settings.pacing.pollMs, 3000)}
          ${renderRangeInputs("Action", "pacing.pageActionMs", settings.pacing.pageActionMs)}
        </div>
        <div class="grab160-row">
          ${renderRangeInputs("Retry", "pacing.bookingRetryMs", settings.pacing.bookingRetryMs, 1000)}
          ${renderRangeInputs("Rate Limit", "pacing.rateLimitCooldownMs", settings.pacing.rateLimitCooldownMs, 15000)}
        </div>
        <label><input data-setting="booking.autoSubmit" type="checkbox" style="width:auto" ${
          settings.booking.autoSubmit ? "checked" : ""
        }> Auto Submit</label>
        <label><input data-setting="booking.autoReturnAfterSubmitFailure" type="checkbox" style="width:auto" ${
          settings.booking.autoReturnAfterSubmitFailure ? "checked" : ""
        }> Auto return after submit failure</label>
        <label>Disease description <input data-setting="booking.diseaseDescription" value="${htmlEscape(settings.booking.diseaseDescription ?? "")}"></label>
        <label>Max submit attempts <input data-setting="booking.maxSubmitAttemptsPerAppointment" type="number" min="1" max="20" value="${settings.booking.maxSubmitAttemptsPerAppointment}"></label>
        <label><input data-setting="session.recoveryEnabled" type="checkbox" style="width:auto" ${
          settings.session.recoveryEnabled ? "checked" : ""
        }> Session Recovery</label>
        <label>Keepalive seconds <input data-setting="session.keepAliveIntervalSeconds" type="number" min="0" value="${settings.session.keepAliveIntervalSeconds}"></label>
        <label>Recovery max attempts <input data-setting="session.recoveryMaxAttempts" type="number" min="0" value="${settings.session.recoveryMaxAttempts}"></label>
        <label>Log Level <select data-setting="logging.level">${LOG_LEVELS.map(
          (level) =>
            `<option value="${level}" ${level === settings.logging.level ? "selected" : ""}>${level}</option>`,
        ).join("")}</select></label>
        <div class="grab160-buttons">
          <button type="button" data-save-settings>Save Settings</button>
          <button type="button" data-reset-settings>Reset Settings</button>
        </div>
      </div>
    `;
  }

  function renderRangeInputs(label, path, value, min = 0) {
    return `<label>${label} min/max ms <span class="grab160-row"><input data-range="${path}" data-range-index="0" type="number" min="${min}" value="${value[0]}"><input data-range="${path}" data-range-index="1" type="number" min="${min}" value="${value[1]}"></span></label>`;
  }

  function renderLogsView(state, settings) {
    const logs = (state.logs ?? []).filter((entry) => shouldLog(entry.level, settings));
    return `<div class="grab160-log">${logs
      .map(
        (entry) =>
          `[${entry.ts}] ${entry.level.toUpperCase()} ${htmlEscape(entry.message)} ${htmlEscape(entry.detail)}`,
      )
      .join("\n")}</div>`;
  }

  function setByPath(target, path, value) {
    const parts = path.split(".");
    let cursor = target;
    for (const part of parts.slice(0, -1)) {
      cursor[part] = cursor[part] ?? {};
      cursor = cursor[part];
    }
    cursor[parts[parts.length - 1]] = value;
  }

  function readSettingInput(container, path, fallback = "") {
    const input = container.querySelector(`[data-setting="${path}"]`);
    if (!input) {
      return fallback;
    }
    if (input.type === "checkbox") {
      return input.checked;
    }
    return input.value;
  }

  function collectSettingsFromPanel(container, previous) {
    const next = clone(previous);
    for (const path of [
      "member.memberId",
      "member.memberLabel",
      "address.province",
      "address.city",
      "address.area",
      "address.detail",
      "filters.startDate",
      "runtime.startAt",
      "booking.autoSubmit",
      "booking.autoReturnAfterSubmitFailure",
      "booking.diseaseDescription",
      "booking.maxSubmitAttemptsPerAppointment",
      "session.recoveryEnabled",
      "session.keepAliveIntervalSeconds",
      "session.recoveryMaxAttempts",
      "logging.level",
    ]) {
      setByPath(next, path, readSettingInput(container, path));
    }
    next.filters.weeks = Array.from(container.querySelectorAll("[data-week]:checked")).map(
      (input) => Number(input.dataset.week),
    );
    next.filters.days = Array.from(container.querySelectorAll("[data-period]:checked")).map(
      (input) => input.dataset.period,
    );
    next.filters.hours = Array.from(container.querySelectorAll("[data-hour-row]"))
      .map((row) => {
        const inputs = row.querySelectorAll("input");
        return `${inputs[0].value}-${inputs[1].value}`;
      })
      .filter((value) => value !== "-");
    for (const path of [
      "pacing.pollMs",
      "pacing.pageActionMs",
      "pacing.bookingRetryMs",
      "pacing.rateLimitCooldownMs",
    ]) {
      const values = Array.from(container.querySelectorAll(`[data-range="${path}"]`))
        .sort((a, b) => Number(a.dataset.rangeIndex) - Number(b.dataset.rangeIndex))
        .map((input) => Number(input.value));
      setByPath(next, path, values);
    }
    return normalizeSettings(next);
  }

  function wireSettingsView(body, settings) {
    const memberInput = body.querySelector('[data-setting="member.memberId"]');
    body.querySelector('[data-setting="member.show"]')?.addEventListener("change", (event) => {
      memberInput.type = event.target.checked ? "text" : "password";
    });
    body.querySelector("[data-add-hour]")?.addEventListener("click", () => {
      const hours = body.querySelector("[data-hours]");
      const row = document.createElement("div");
      row.className = "grab160-row";
      row.dataset.hourRow = "1";
      row.innerHTML =
        '<input type="time" step="1800" value="08:00"><input type="time" step="1800" value="09:00"><button type="button" data-remove-hour>Delete</button>';
      hours.appendChild(row);
      row.querySelector("[data-remove-hour]").addEventListener("click", () => row.remove());
    });
    body.querySelectorAll("[data-remove-hour]").forEach((button) => {
      button.addEventListener("click", () => button.closest("[data-hour-row]")?.remove());
    });
    body.querySelector("[data-save-settings]")?.addEventListener("click", () => {
      try {
        const next = collectSettingsFromPanel(body, settings);
        if (next.booking.autoSubmit && !settings.booking.autoSubmit) {
          const confirmed = globalThis.confirm?.(
            "autoSubmit will click the final booking submit control automatically. Continue?",
          );
          if (!confirmed) {
            return;
          }
        }
        writeSettings(next);
        setSummary("info", "Settings saved.", "", { force: true });
      } catch (error) {
        setSummary("error", "Settings validation failed.", error.message);
      }
    });
    body.querySelector("[data-reset-settings]")?.addEventListener("click", () => {
      resetSettings();
      setSummary("info", "Settings reset to defaults.", "", { force: true });
    });
  }

  function bootstrapController(controllerId = null) {
    renderPanel();
    const doctorTarget = parseDoctorPageUrl(location.href);
    if (doctorTarget) {
      const claimedControllerId = controllerId || claimPageController("doctor");
      if (!claimedControllerId) {
        return;
      }
      void runDoctorPageController(claimedControllerId).catch((error) => {
        stopRun(`Userscript crashed: ${error?.message || error}`);
      });
      return;
    }
    const bookingTarget = parseBookingUrl(location.href);
    if (bookingTarget) {
      const claimedControllerId = controllerId || claimPageController("booking");
      if (!claimedControllerId) {
        return;
      }
      void runBookingPageController(claimedControllerId).catch((error) => {
        stopRun(`Userscript crashed: ${error?.message || error}`);
      });
      return;
    }
    setSummary(
      "warn",
      "This userscript only supports 91160 doctor detail and ystep1 pages.",
      location.href,
    );
  }

  globalThis.__GRAB160_DOCTOR_POLLER_TEST_HOOKS__ = {
    CONFIG_DEFAULTS,
    SETTINGS_KEY,
    STATE_KEY,
    normalizeOptionalValue,
    normalizeSettings,
    normalizeHourValue,
    normalizeHours,
    readCookieValue,
    resolveCurrentUserKey,
    findCurrentUserKey,
    buildUrlWithParams,
    fetchJsonInsidePage,
    makeControllerId,
    isControllerActive,
    claimPageController,
    prepareManualControllerStart,
    parseDoctorPageUrl,
    parseBookingUrl,
    buildDoctorUrl,
    buildBookingUrl,
    resolveTargetFromSnapshot,
    parseDoctorSchedulePayload,
    filterSlots,
    pickNextSlot,
    extractRateLimitMessage,
    chooseAppointmentOption,
    appointmentKey,
    parseBookingFormState,
    findSelectOption,
    fillAddressSelection,
    fillClinicId,
    readScheduleDateFromSerializedData,
    fillScheduleDate,
    fillBookingForm,
    installCheckIdInfoBlankResponsePatch,
    normalizeCheckIdInfoJsonResponse,
    findSubmitControl,
    triggerSubmitControl,
    markSubmitInProgress,
    resolveMemberSelection,
    memberRadioDebugSummary,
    inspectBookingPage,
  };

  if (DISABLE_AUTO_START) {
    return;
  }

  if (readSettings().runtime.autoStart && !readState().running) {
    patchState((state) => ({ ...state, running: true }));
  }
  bootstrapController();
})();
