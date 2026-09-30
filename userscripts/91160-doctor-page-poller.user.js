// ==UserScript==
// @name         160Grab 91160 Doctor Page Poller
// @namespace    https://github.com/wufei-png/160Grab
// @version      0.3.0
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
  const PANEL_TOOLTIP_ID = "grab160-doctor-page-poller-tooltip";
  const SCRIPT_VERSION = "0.3.0";
  const PLACEHOLDER_VALUES = new Set(["", "...", "null", "undefined", "<member_id>"]);
  const RATE_LIMIT_PATTERNS = [
    "单位时间内访问次数过多",
    "访问次数过多",
    "访问过于频繁",
    "操作过于频繁",
  ];
  const LOG_LEVELS = ["debug", "info", "warn", "error"];
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
      province: null,
      city: null,
      area: null,
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
      bookingSubmitSettleMs: [3500, 4500],
      bookingRetryMs: [2000, 4000],
      rateLimitCooldownMs: [15000, 25000],
    },
    booking: {
      submitMode: "auto",
      autoSubmit: true,
      maxPreSubmitAttempts: 3,
      autoReturnAfterSubmitFailure: false,
      diseaseDescription: null,
      clinicCard: null,
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
    const sourceBooking = rawSettings?.booking ?? {};
    // Old generated defaults are indistinguishable from explicit choices. Clear
    // those exact values on migration; re-entry in v4 records an explicit choice.
    if (rawSettings?.settingsVersion !== 4) {
      for (const [key, old] of Object.entries({province:"广东",city:"深圳",area:"南山区"})) {
        if (merged.address[key] === old) merged.address[key] = null;
      }
      if (merged.booking.diseaseDescription === "门诊就诊，具体病情现场面诊沟通") merged.booking.diseaseDescription = null;
    }
    const submitMode = sourceBooking.submitMode === undefined
      ? (sourceBooking.autoSubmit === false ? "manual_confirm" : "auto")
      : sourceBooking.submitMode;
    if (!["auto", "manual_confirm"].includes(submitMode)) throw new Error("Invalid submission mode");
    return {
      settingsVersion: 4,
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
        bookingSubmitSettleMs: normalizeRange(
          merged.pacing.bookingSubmitSettleMs,
          CONFIG_DEFAULTS.pacing.bookingSubmitSettleMs,
          { min: 0 },
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
        submitMode,
        autoSubmit: submitMode === "auto",
        maxPreSubmitAttempts: Math.min(3, normalizeInteger(
          sourceBooking.maxPreSubmitAttempts ?? sourceBooking.maxSubmitAttemptsPerAppointment,
          CONFIG_DEFAULTS.booking.maxPreSubmitAttempts,
          { min: 1, max: 20 },
        )),
        autoReturnAfterSubmitFailure: normalizeBoolean(
          merged.booking.autoReturnAfterSubmitFailure,
          false,
        ),
        diseaseDescription:
          normalizeOptionalValue(merged.booking.diseaseDescription),
        clinicCard: normalizeOptionalValue(merged.booking.clinicCard),
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

  const LOG_SCHEMA = 1;
  // Registered fixed workflow messages only; runtime text is never registered.
  const SAFE_LOG_MESSAGES = new Set([
    "Address selection failed.",
    "Booking form did not become ready before submit.",
    "Booking form preparation failed; manual action required.",
    "Booking form prepared; waiting for manual submit.",
    "Booking form invalid.",
    "Booking page hit rate limiting.",
    "Booking submit failed.",
    "Booking page loaded while runner is stopped.",
    "Booking submit failed; staying on page.",
    "Booking succeeded.",
    "Cannot return to doctor page; target is incomplete.",
    "Controller is already active; not starting another loop.",
    "Could not find a submit control on the booking page.",
    "Diagnostic event.",
    "Doctor target resolution failed.",
    "Login expired; manual login required.",
    "Matched slot; opening booking page.",
    "Member selection failed.",
    "No bookable matching slot yet.",
    "Page fetch schedule request failed.",
    "Page jQuery schedule request failed; trying fetch.",
    "Ready. Press Start to poll this doctor page.",
    "Redirecting to canonical doctor page.",
    "Schedule date fill failed.",
    "Schedule polling hit rate limiting.",
    "Schedule polling request failed.",
    "Selected member blocked before submit.",
    "Selected member cannot submit booking.",
    "Session looks expired. Refreshing doctor page before retrying.",
    "Session recovery disabled; manual login required.",
    "Settings reset to defaults.",
    "Settings saved.",
    "Settings validation failed.",
    "Started.",
    "Stopped.",
    "Submit attempts exhausted; manual action required.",
    "Submitted booking form.",
    "Submitted booking form; runner paused to avoid duplicate submit.",
    "Submission outcome unknown; verify original site records.",
    "This userscript only supports 91160 doctor detail and ystep1 pages.",
    "Triggered booking follow-up action.",
    "Unsupported booking page URL.",
    "Userscript failed; manual action required.",
    "Waited for booking page initialization before submit.",
    "Waiting for booking page initialization before submit.",
    "Waiting for configured start time.",
  ]);
  const SAFE_DETAIL_COUNTS = new Set(["attempt", "pollAttempt", "delayMs", "slotCount", "count", "status"]);
  const SAFE_DETAIL_FLAGS = new Set(["ready", "success", "ticketPresent", "randstrPresent"]);

  function safeLogDetail(detail) {
    if (!isPlainObject(detail)) return "";
    const output = {};
    for (const [key, value] of Object.entries(detail)) {
      if (SAFE_DETAIL_COUNTS.has(key) && typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1e9) output[key] = value;
      if (SAFE_DETAIL_FLAGS.has(key) && typeof value === "boolean") output[key] = value;
    }
    return JSON.stringify(output);
  }

  function safeLogEntry(entry) {
    if (!isPlainObject(entry) || entry.schema !== LOG_SCHEMA) return null;
    const ts = Date.parse(entry.ts);
    if (!Number.isFinite(ts) || ts < Date.now() - 7 * 86400000 || ts > Date.now()) return null;
    let detail = {};
    try { detail = JSON.parse(entry.detail); } catch (_error) { /* discard */ }
    return { schema: LOG_SCHEMA, ts: new Date(ts).toISOString(), level: LOG_LEVELS.includes(entry.level) ? entry.level : "info", message: SAFE_LOG_MESSAGES.has(entry.message) ? entry.message : "Diagnostic event.", detail: safeLogDetail(detail) };
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
      const raw = readStoredValue(SETTINGS_KEY, null);
      const settings = normalizeSettings(raw);
      if (raw && raw.settingsVersion !== 4) writeStoredValue(SETTINGS_KEY, settings);
      return settings;
    } catch (error) {
      console.error("[160Grab error] Failed to load stored settings; using defaults.");
      return normalizeSettings({booking:{submitMode:"manual_confirm"}});
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

  const JOURNAL_KEY = "grab160.submissionJournal.v1";
  const POLICY_VERSION = "submit-v1";
  const UNRESOLVED = new Set(["SUBMITTING", "OUTCOME_UNKNOWN"]);
  const TERMINAL = new Set(["CONFIRMED_SUCCESS", "CONFIRMED_NO_EFFECT"]);

  function randomRef(bytes = 16) {
    return Array.from(crypto.getRandomValues(new Uint8Array(bytes)), (b) => b.toString(16).padStart(2, "0")).join("");
  }

  function validateJournal(journal) {
    const keys = (value, expected) => isPlainObject(value) && Object.keys(value).sort().join() === expected.sort().join();
    if (!keys(journal, ["version", "salt", "attempts", "consents", "audit"]) || journal.version !== 1 ||
        !/^[a-f0-9]{64}$/.test(journal.salt) || !Array.isArray(journal.attempts) || !Array.isArray(journal.consents) || !Array.isArray(journal.audit)) throw new Error("storage");
    for (const r of journal.attempts) {
      if (!keys(r, ["attempt_id", "booking_ref", "state", "created_at", "updated_at", "evidence_type", "failure_class", "human_action_required"]) ||
          !/^[a-f0-9]{32}$/.test(r.attempt_id) || !/^[a-f0-9]{64}$/.test(r.booking_ref) ||
          !(UNRESOLVED.has(r.state) || TERMINAL.has(r.state)) ||
          !Number.isFinite(Date.parse(r.created_at)) || !Number.isFinite(Date.parse(r.updated_at)) ||
          !["none", "matched_business", "human_verified"].includes(r.evidence_type) ||
          ![null, "post_submit", "business_rejected", "interrupted"].includes(r.failure_class) || typeof r.human_action_required !== "boolean" || r.human_action_required !== UNRESOLVED.has(r.state) || TERMINAL.has(r.state) !== (r.evidence_type !== "none")) throw new Error("storage");
    }
    for (const r of journal.consents) {
      if (!keys(r, ["binding_ref", "policy_version"]) || !/^[a-f0-9]{64}$/.test(r.binding_ref) || !["submit-v0", POLICY_VERSION].includes(r.policy_version)) throw new Error("storage");
    }
    for (const r of journal.audit) {
      if (!keys(r, ["attempt_id", "state", "at"]) || !/^[a-f0-9]{32}$/.test(r.attempt_id) || !TERMINAL.has(r.state) || !Number.isFinite(Date.parse(r.at))) throw new Error("storage");
    }
  }

  function writeJournal(journal) {
    validateJournal(journal);
    const raw = JSON.stringify(journal);
    if (!globalThis.localStorage) throw new Error("storage");
    // One synchronous atomic storage item, with read-back; never fall back to a
    // different namespace or silently create a fresh journal after an error.
    localStorage.setItem(JOURNAL_KEY, raw);
    if (localStorage.getItem(JOURNAL_KEY) !== raw) throw new Error("storage");
  }

  function readJournal() {
    if (!globalThis.localStorage) throw new Error("storage");
    const raw = localStorage.getItem(JOURNAL_KEY);
    if (raw !== null) {
      const journal = JSON.parse(raw);
      validateJournal(journal);
      return journal;
    }
    const journal = { version: 1, salt: randomRef(32), attempts: [], consents: [], audit: [] };
    const legacyRaw = globalThis.sessionStorage?.getItem(STATE_KEY);
    const legacy = legacyRaw ? JSON.parse(legacyRaw) : null;
    if (legacy?.submittingBooking) {
      const now = new Date().toISOString();
      journal.attempts.push({ attempt_id: randomRef(), booking_ref: randomRef(32), state: "OUTCOME_UNKNOWN", created_at: now, updated_at: now, evidence_type: "none", failure_class: "interrupted", human_action_required: true });
    }
    writeJournal(journal);
    return journal;
  }

  function submissionBlocked() {
    try { return readJournal().attempts.some((r) => UNRESOLVED.has(r.state)); }
    catch (_error) { return true; }
  }

  async function journalReference(parts) {
    const journal = readJournal();
    const salt = Uint8Array.from(journal.salt.match(/../g), (v) => parseInt(v, 16));
    const key = await crypto.subtle.importKey("raw", salt, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const digest = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(JSON.stringify(parts)));
    return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
  }

  async function beginAttempt(parts, guard = () => true) {
    const bookingRef = await journalReference(parts);
    const journal = readJournal();
    if (!guard()) throw new Error("cancelled");
    if (journal.attempts.some((r) => UNRESOLVED.has(r.state) || r.booking_ref === bookingRef)) throw new Error("blocked");
    const now = new Date().toISOString();
    const record = { attempt_id: randomRef(), booking_ref: bookingRef, state: "SUBMITTING", created_at: now, updated_at: now, evidence_type: "none", failure_class: null, human_action_required: true };
    journal.attempts.push(record);
    writeJournal(journal);
    return record;
  }

  function finishAttempt(attemptId, outcome, human = false) {
    if (!(TERMINAL.has(outcome) || outcome === "OUTCOME_UNKNOWN")) throw new Error("storage");
    const journal = readJournal();
    const record = journal.attempts.find((r) => r.attempt_id === attemptId);
    if (!record || !UNRESOLVED.has(record.state)) throw new Error("storage");
    Object.assign(record, { state: outcome, updated_at: new Date().toISOString(), evidence_type: human ? "human_verified" : TERMINAL.has(outcome) ? "matched_business" : "none", failure_class: outcome === "OUTCOME_UNKNOWN" ? "post_submit" : outcome === "CONFIRMED_NO_EFFECT" ? "business_rejected" : null, human_action_required: outcome === "OUTCOME_UNKNOWN" });
    if (human) journal.audit.push({ attempt_id: attemptId, state: outcome, at: new Date().toISOString() });
    writeJournal(journal);
  }

  function resolvePending(booked) {
    if (!globalThis.confirm("请先在原站预约记录核对每个未决预约。确认核对结果为" + (booked ? "已预约" : "未预约") + "？")) return;
    for (const r of readJournal().attempts.filter((r) => UNRESOLVED.has(r.state))) finishAttempt(r.attempt_id, booked ? "CONFIRMED_SUCCESS" : "CONFIRMED_NO_EFFECT", true);
    stopRun();
    renderPanel();
  }

  let consentRunNonce = null;
  let consentDenied = false;
  let activeConsentBinding = null;

  async function ensureSubmissionConsent(target, memberSelection, { accountRef = null, interactive = false } = {}) {
    if (consentDenied || submissionBlocked()) return false;
    if (!isCompleteTarget(target) || !memberSelection.memberId) return false;
    consentRunNonce ??= randomRef();
    const bindingRef = await journalReference([accountRef || consentRunNonce, memberSelection.memberId, target.unitId, target.depId, target.doctorId, POLICY_VERSION]);
    const journal = readJournal();
    if (journal.consents.some((c) => c.binding_ref === bindingRef && c.policy_version === POLICY_VERSION)) {
      activeConsentBinding = bindingRef;
      return true;
    }
    if (!interactive || typeof globalThis.confirm !== "function") return false;
    const accepted = globalThis.confirm(
      `医生目标：${target.unitId}/${target.depId}/${target.doctorId}；就诊人：${memberSelection.memberId}。\n` +
      "自动模式会点击最终预约提交；未知结果必须在原站核对预约记录。\n" +
      "禁止与 Python、其他浏览器或多机器混跑同一目标。\n" +
      "此授权不替代站点协议、验证码或支付确认。\n" +
      (accountRef ? "授权保存于本机。" : "账号不能可靠区分，授权仅本次页面运行有效，刷新/重启需再确认。")
    );
    if (!accepted) {
      consentDenied = true;
      const settings = readSettings();
      settings.booking.submitMode = "manual_confirm";
      writeSettings(settings);
      return false;
    }
    if (submissionBlocked()) return false;
    const latest = readJournal();
    latest.consents.push({ binding_ref: bindingRef, policy_version: POLICY_VERSION });
    writeJournal(latest);
    activeConsentBinding = bindingRef;
    return true;
  }

  function consentStillValid() {
    if (consentDenied || !activeConsentBinding || submissionBlocked()) return false;
    return readJournal().consents.some((c) => c.binding_ref === activeConsentBinding && c.policy_version === POLICY_VERSION);
  }

  function revokeConsent() {
    const journal = readJournal();
    journal.consents = [];
    writeJournal(journal);
    consentRunNonce = null;
    activeConsentBinding = null;
    consentDenied = true;
    stopRun();
    renderPanel();
  }

  function defaultState() {
    return {
      version: 2,
      running: false,
      controllerId: null,
      activeView: "main",
      pollAttempt: 0,
      preSubmitFailures: 0,
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
      const state = raw ? { ...defaultState(), ...JSON.parse(raw) } : defaultState();
      const blocked = submissionBlocked();
      let latest = null;
      try { latest = readJournal().attempts.at(-1); } catch (_error) { /* fail closed */ }
      state.outcome = blocked ? "OUTCOME_UNKNOWN" : UNRESOLVED.has(state.outcome) ? latest?.state ?? "DISCOVERED" : state.outcome ?? latest?.state ?? "DISCOVERED";
      if (blocked) { state.running = false; state.submittingBooking = { state: "OUTCOME_UNKNOWN" }; }
      else { state.submittingBooking = null; }
      state.logs = Array.isArray(state.logs) ? state.logs.map(safeLogEntry).filter(Boolean) : [];
      state.summary = safeLogEntry(state.summary);
      // Persist the migration immediately, even when no new log is appended.
      globalThis.sessionStorage?.setItem(STATE_KEY, JSON.stringify(state));
      return state;
    } catch (_error) {
      return defaultState();
    }
  }

  function writeState(state) {
    const next = {
      ...defaultState(),
      ...state,
      submitAttempts: { ...(state.submitAttempts ?? {}) },
      logs: Array.isArray(state.logs) ? state.logs.map(safeLogEntry).filter(Boolean) : [],
      summary: safeLogEntry(state.summary),
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
    if (submissionBlocked()) return false;
    const state = readState();
    return Boolean(controllerId && state.running && state.controllerId === controllerId);
  }

  function claimPageController(kind) {
    if (submissionBlocked()) return null;
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
    if (submissionBlocked()) return null;
    consentRunNonce = null;
    activeConsentBinding = null;
    consentDenied = false;
    const controllerId = makeControllerId(kind);
    activeControllerId = controllerId;
    patchState((next) => ({
      ...next,
      running: true,
      outcome: "DISCOVERED",
      interactiveConsent: true,
      preSubmitFailures: 0,
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
      schema: LOG_SCHEMA,
      ts: new Date().toISOString(),
      level: LOG_LEVELS.includes(level) ? level : "info",
      message: SAFE_LOG_MESSAGES.has(message) ? message : "Diagnostic event.",
      detail: safeLogDetail(detail),
    };
    if (shouldLog(entry.level, settings)) {
      const method = entry.level === "error" ? "error" : entry.level === "warn" ? "warn" : "log";
      console[method](`[160Grab ${entry.level}] ${entry.message}`, entry.detail);
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

  function panelPhase(state = readState(), summary = state.summary) {
    if (state.outcome === "OUTCOME_UNKNOWN") return "error";
    const message = compactText(summary?.message).toLowerCase();
    if (
      summary?.level === "error" ||
      message.includes("failed") ||
      message.includes("cannot") ||
      message.includes("invalid")
    ) {
      return "error";
    }
    if (message.includes("rate limiting") || message.includes("returning to doctor page")) {
      return "cooldown";
    }
    if (state.submittingBooking || message.includes("submitted booking")) {
      return "submit";
    }
    if (
      state.pendingBooking ||
      message.includes("matched slot") ||
      message.includes("booking form prepared")
    ) {
      return "hit";
    }
    return state.running ? "polling" : "idle";
  }

  function panelPhaseLabel(phase) {
    return (
      {
        idle: "待命",
        polling: "轮询",
        hit: "命中",
        submit: "提交",
        cooldown: "冷却",
        error: "异常",
      }[phase] ?? "待命"
    );
  }

  function formatPanelDetail(detail) {
    if (detail === null || detail === undefined || detail === "") {
      return "";
    }
    if (typeof detail === "string") {
      return compactText(detail);
    }
    try {
      return compactText(JSON.stringify(detail));
    } catch (_error) {
      return compactText(detail);
    }
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
    const phase = panelPhase(state, summary);
    const phaseLabel = body.querySelector("[data-panel-phase-label]");
    if (phaseLabel) {
      phaseLabel.textContent = panelPhaseLabel(phase);
    }
    body.querySelectorAll("[data-panel-stage]").forEach((stage) => {
      stage.classList.toggle("grab160-stage-active", stage.dataset.panelStage === phase);
    });
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
      detail.textContent = formatPanelDetail(summary?.detail);
    }
    const running = body.querySelector("[data-panel-running]");
    if (running) {
      running.textContent = state.outcome ?? "DISCOVERED";
    }
    const autoSubmit = body.querySelector("[data-panel-auto-submit]");
    if (autoSubmit) {
      autoSubmit.textContent = settings.booking.autoSubmit ? "ON" : "off";
      autoSubmit.classList.toggle("grab160-danger-text", settings.booking.autoSubmit);
    }
    const attempts = body.querySelector("[data-panel-attempts]");
    if (attempts) {
      attempts.textContent = String(state.pollAttempt);
    }
    const recoveryAttempts = body.querySelector("[data-panel-recovery-attempts]");
    if (recoveryAttempts) {
      recoveryAttempts.textContent = `${state.sessionRecoveryAttempts}/${settings.session.recoveryMaxAttempts}`;
    }
    const target = body.querySelector("[data-panel-target]");
    if (target) {
      target.textContent = panelDoctorText(state);
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
    const scheduleInputs = Array.from(document.querySelectorAll('input[name="schedule_id"]'));
    const scheduleId = scheduleInputs.length === 1 ? compactText(scheduleInputs[0].value) : "";
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

  // Private form snapshots/decisions: values never enter logs or the journal.
  const FORM_FIELDS = {
    card: '#hismemid, [name="hisMemId"], [name="hismemid"]',
    date: '#sch_date, [name="sch_date"]',
    disease_input: '#disease_input, [name="disease_input"]',
    disease_content: '#disease_content, [name="disease_content"]',
    'address.province': '#useraddress_province',
    'address.city': '#useraddress_city',
    'address.area': '#useraddress_area, select[name="addressId"]',
    'address.detail': '#useraddress_detail, input[name="address"]',
    accept: '#check_yuyue_rule, input[name="accept"][value="1"]',
  };
  const SUBMIT_SELECTOR = '#submitbtn, #submit_booking, #submitBooking, #suborder button[type="submit"], #suborder input[type="submit"]';

  function formActionable(node) {
    return Boolean(node && node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden' && !node.matches(':disabled') && !node.closest('[aria-disabled="true"]'));
  }

  function scheduleRecordDates(raw, scheduleId) {
    try {
      const bytes = new TextEncoder().encode(raw);
      if (bytes.length > 1000000) return [];
      const decoder = new TextDecoder('utf-8', {fatal:true});
      let pos = 0, remaining = 10000;
      const expect = token => {
        for (const char of token) if (bytes[pos++] !== char.charCodeAt(0)) throw new Error('Malformed array');
      };
      const until = delimiter => {
        const start = pos;
        while (pos < bytes.length && bytes[pos] !== delimiter.charCodeAt(0)) pos++;
        if (pos === bytes.length) throw new Error('Malformed array');
        const value = decoder.decode(bytes.slice(start,pos)); pos++;
        return value;
      };
      const integer = text => {
        if (!/^-?\d+$/.test(text) || !Number.isSafeInteger(Number(text))) throw new Error('Invalid integer');
        return Number(text);
      };
      const read = (depth=0) => {
        if (depth > 32 || --remaining < 0) throw new Error('Array budget exceeded');
        const kind = String.fromCharCode(bytes[pos++]);
        if (kind === 'N') {expect(';'); return null;}
        expect(':');
        if (kind === 's') {
          const size = integer(until(':')); if (size < 0) throw new Error('Invalid string length');
          expect('"'); const value = decoder.decode(bytes.slice(pos,pos+size)); pos += size; expect('";'); return value;
        }
        if (kind === 'a') {
          const size = integer(until(':')); if (size < 0 || size > 10000) throw new Error('Invalid array length');
          expect('{'); const pairs = [];
          for (let i=0;i<size;i++) pairs.push([read(depth+1),read(depth+1)]);
          expect('}'); return pairs;
        }
        if (kind === 'i' || kind === 'b') return integer(until(';'));
        if (kind === 'd') {const number = Number(until(';')); if (!Number.isFinite(number)) throw new Error('Invalid number'); return number;}
        throw new Error('Unsupported serialized type');
      };
      const root = read();
      if (pos !== bytes.length || !Array.isArray(root)) return [];
      const records = root.filter(([k]) => String(k) === scheduleId).map(([,v]) => v);
      if (records.length !== 1 || !Array.isArray(records[0])) return [];
      const dates = [];
      const visit = pairs => pairs.forEach(([key,value]) => {
        if (key === 'to_date' && typeof value === 'string') dates.push(value);
        else if (Array.isArray(value)) visit(value);
      });
      visit(records[0]); return dates;
    } catch (_error) {return [];}
  }

  function readBookingSnapshot() {
    const all = selector => Array.from(document.querySelectorAll(selector));
    const snapshot = {version:1, schedule_ids:all('[name="schedule_id"]').map(n => n.value.trim()), members:[], hidden_members:[], times:[], dates:[], fields:{}, submit:all(SUBMIT_SELECTOR).map(formActionable), other_required:[]};
    snapshot.hidden_members = all('input[type="hidden"]').filter(n => ['member_id','memberId','mid','his_mem_id'].includes(n.name)).map(n => n.value.trim());
    all('input[type="radio"]').forEach((n, index) => {
      if (!['mid','member_id','memberid','his_mem_id'].includes(n.name.toLowerCase()) && !n.hasAttribute('data-member-id') && !n.hasAttribute('data-mid')) return;
      const container = n.closest('tr,li,label,.patient_item,.member_item,.person_item') || n.parentElement;
      snapshot.members.push({ids:[...new Set([n.value.trim(), n.getAttribute('data-member-id'), n.getAttribute('data-mid')].filter(Boolean))], label:compactText(container.textContent), index,
        actionable:formActionable(n), checked:n.checked,
        blocked:n.getAttribute('need_check') === '1' || ['record_created','is_complete','is_info_complete'].some(k => n.getAttribute(k) === '0') || /暂不能预约|审核中|认证|建档/.test((n.getAttribute('data-title') || '') + (n.title || '')),
        address:Object.fromEntries([['province','province_id'],['city','city_id'],['area','area_id'],['detail','address']].map(([k,a]) => [k,n.getAttribute(a) || '']))});
    });
    snapshot.times = all('#delts li[val]').map(n => ({value:n.getAttribute('val').trim(),label:compactText(n.textContent),actionable:formActionable(n),selected:n.classList.contains('selected')}));
    const known = new Set();
    Object.entries(FORM_FIELDS).forEach(([key, selector]) => {
      const nodes = all(selector).filter(n => ['INPUT','SELECT','TEXTAREA'].includes(n.tagName));
      if (!nodes.length) return;
      snapshot.fields[key] = nodes.map(n => {
        known.add(n);
        return {value:n.value.trim(), checked:Boolean(n.checked), actionable:!n.matches(':disabled') && !n.readOnly && (formActionable(n) || n.type === 'hidden'), kind:n.tagName.toLowerCase(),
          options:Array.from(n.options || []).map(o => ({value:o.value,label:compactText(o.textContent),disabled:o.disabled || (o.parentElement.tagName === 'OPTGROUP' && o.parentElement.disabled)}))};
      });
    });
    snapshot.other_required = all('[required]').filter(n => !known.has(n) && !['schedule_id','member_id','memberId','mid','his_mem_id'].includes(n.name)).map(n => n.validity ? n.validity.valid : Boolean(n.value?.trim()));
    all('[name="sch_data"]').forEach(n => {
      if (snapshot.schedule_ids.length === 1) {
        snapshot.dates.push(...scheduleRecordDates(n.value, snapshot.schedule_ids[0]));
      }
    });
    all('#jzdate').forEach(n => snapshot.dates.push(...Array.from(n.parentElement.textContent.matchAll(/(20\d{2})年(\d{2})月(\d{2})日/g), m => `${m[1]}-${m[2]}-${m[3]}`)));
    return snapshot;
  }

  function validFormDate(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return false;
    const parsed = new Date(value + 'T00:00:00Z');
    return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0,10) === value;
  }

  function decideBookingPreparation(snapshot, selection, values = {}) {
    const blockers = [], writes = [], sources = {};
    let memberId = selection.member_id || null, memberIndex = null, member = null;
    const members = snapshot.members;
    let matches = memberId ? members.filter(m => m.ids.includes(memberId)) : [];
    if (!memberId && selection.member_label) matches = members.filter(m => m.label === selection.member_label);
    if (!memberId && !selection.member_label) matches = members.length === 1 ? members : [];
    if (matches.length === 1) {
      member = matches[0]; memberId ||= member.ids[0]; memberIndex = member.index;
      if (member.ids.length !== 1 || !member.actionable || member.blocked) blockers.push('member.blocked');
    } else if (!matches.length && !members.length && memberId && JSON.stringify(snapshot.hidden_members) === JSON.stringify([memberId])) {
      member = null;
    } else if (!matches.length && !members.length && !memberId && snapshot.hidden_members.length === 1 && snapshot.hidden_members[0]) {
      memberId = snapshot.hidden_members[0];
    } else blockers.push(memberId ? 'member.mismatch' : 'member.ambiguous');
    if (snapshot.schedule_ids.length !== 1 || snapshot.schedule_ids[0] !== selection.schedule_id) blockers.push('schedule.mismatch');
    const timeValue = selection.appointment_value ?? null;
    const times = snapshot.times.filter(t => t.value === timeValue);
    if ((snapshot.times.length || timeValue) && (times.length !== 1 || !times[0].actionable)) blockers.push('time.mismatch');
    const dates = [...new Set([...snapshot.dates, selection.date].filter(Boolean))];
    if (dates.some(d => !validFormDate(d)) || dates.length > 1) blockers.push('date.conflict');
    Object.entries(snapshot.fields).forEach(([key, controls]) => {
      if (controls.length !== 1) { blockers.push(key + '.ambiguous'); return; }
      const f = controls[0];
      let existing = f.value, desired = values[key] || '', source = desired ? 'config' : null;
      if (key.startsWith('address.') && member) {
        const memberValue = member.address[key.split('.')[1]] || '';
        if (memberValue) {
          if (desired && desired !== memberValue) {
            const options = f.options.filter(o => o.value === desired || o.label === desired);
            if (!(options.length === 1 && options[0].value === memberValue)) blockers.push(key + '.conflict');
          }
          desired = memberValue; source = 'member';
        }
      }
      if (key === 'date') { desired = dates.length === 1 ? dates[0] : ''; source = desired ? 'schedule' : null; }
      if (key === 'accept') {
        if (!f.checked) blockers.push('accept.required');
        sources[key] = f.checked ? 'existing' : 'missing'; return;
      }
      if (f.kind === 'select') {
        if (existing === '0') existing = '';
        if (desired) {
          const options = f.options.filter(o => !o.disabled && !['','0'].includes(o.value) && (o.value === desired || o.label === desired));
          if (options.length !== 1) { blockers.push(key + '.option'); return; }
          desired = options[0].value;
        }
      }
      if (existing) {
        sources[key] = 'existing';
        if ((desired && desired !== existing) || (key === 'date' && !validFormDate(existing))) blockers.push(key + '.conflict');
      } else if (desired) {
        sources[key] = source;
        if (!f.actionable) blockers.push(key + '.disabled');
        else writes.push({field:key,value:desired,kind:f.kind});
      } else { sources[key] = 'missing'; blockers.push(key + '.required'); }
    });
    if (!snapshot.other_required.every(Boolean)) blockers.push('other.required');
    if (JSON.stringify(snapshot.submit) !== '[true]') blockers.push('submit.control');
    const unique = [...new Set(blockers)].sort();
    const hard = unique.some(b => !b.endsWith('.required') && !b.endsWith('.option'));
    return {state:unique.length ? 'AWAITING_MANUAL_CONFIRMATION' : 'PREPARED',blockers:unique, member_id:memberId, member_index:memberIndex, schedule_id:selection.schedule_id,appointment_value:timeValue,sources,writes:hard ? [] : writes,can_prepare:!hard};
  }

  function formSelection(formState, memberSelection) {
    return {member_id:memberSelection.memberId, schedule_id:formState.scheduleId, appointment_value:formState.appointmentValue ?? null, date:formState.expectedDate ?? null};
  }

  function formValues(addressConfig = {}, bookingConfig = {}) {
    return {card:bookingConfig.clinicCard, disease_input:bookingConfig.diseaseDescription, disease_content:bookingConfig.diseaseDescription,
      ...Object.fromEntries(['province','city','area','detail'].map(k => ['address.' + k,addressConfig[k]]))};
  }

  function selectionReady(snapshot, decision) {
    const checked = snapshot.members.filter(m => m.checked);
    const memberReady = (decision.member_index === null || (checked.length === 1 && checked[0].index === decision.member_index)) && snapshot.hidden_members.every(v => v === decision.member_id);
    return memberReady && (!decision.appointment_value || JSON.stringify(snapshot.times.filter(t => t.selected).map(t => t.value)) === JSON.stringify([decision.appointment_value]));
  }

  function applyBookingDecision(snapshot, decision) {
    if (!decision.can_prepare) return;
    if (decision.member_index !== null) {
      const matches = Array.from(document.querySelectorAll('input[type="radio"]')).filter(n => n.value === decision.member_id);
      if (matches.length !== 1 || !formActionable(matches[0])) return;
      if (!matches[0].checked) matches[0].click();
    }
    if (decision.appointment_value) {
      const matches = Array.from(document.querySelectorAll('#delts li[val]')).filter(n => n.getAttribute('val') === decision.appointment_value);
      if (matches.length !== 1 || !formActionable(matches[0])) return;
      if (!matches[0].classList.contains('selected')) matches[0].click();
    }
    for (const write of decision.writes) {
      const controls = Array.from(document.querySelectorAll(FORM_FIELDS[write.field]));
      if (controls.length !== 1) return;
      const n = controls[0];
      if (n.value.trim() && !(write.kind === 'select' && n.value === '0')) continue;
      if (n.matches(':disabled') || n.readOnly) return;
      n.value = write.value;
      n.dispatchEvent(new Event('input', {bubbles:true}));
      n.dispatchEvent(new Event('change', {bubbles:true}));
    }
  }

  function resolveMemberSelection(memberConfig) {
    const snapshot = readBookingSnapshot();
    const decision = decideBookingPreparation(snapshot, {member_id:memberConfig.memberId,member_label:memberConfig.memberLabel,schedule_id:snapshot.schedule_ids[0],appointment_value:snapshot.times[0]?.value});
    if (decision.blockers.some(b => b.startsWith('member.'))) return {ok:false, reason:'Member selection requires manual action.'};
    return {ok:true,memberId:decision.member_id,radio:decision.member_index === null ? null : document.querySelectorAll('input[type="radio"]')[decision.member_index]};
  }

  function readBookingFormReadiness(formState, memberSelection, addressConfig, bookingConfig) {
    const snapshot = readBookingSnapshot();
    const decision = decideBookingPreparation(snapshot, formSelection(formState, memberSelection), formValues(addressConfig, bookingConfig));
    return {ok:!decision.blockers.length && !decision.writes.length && selectionReady(snapshot, decision),missing:decision.blockers,decision};
  }

  async function prepareBookingFormForSubmit(formState, memberSelection, addressConfig, bookingConfig = CONFIG_DEFAULTS.booking, options = {}) {
    const attempts = Math.min(3, Math.max(1, Number(options.attempts ?? bookingConfig.maxPreSubmitAttempts ?? 3)));
    const delayMs = Math.max(0, Number(options.delayMs ?? 250));
    let readiness;
    for (let attempt = 1; attempt <= attempts; attempt++) {
      const snapshot = readBookingSnapshot();
      const decision = decideBookingPreparation(snapshot, formSelection(formState, memberSelection), formValues(addressConfig, bookingConfig));
      applyBookingDecision(snapshot, decision);
      readiness = readBookingFormReadiness(formState, memberSelection, addressConfig, bookingConfig);
      if (readiness.ok) return {ok:true, attempt, readiness};
      if (decision.blockers.some(b => /\.(conflict|ambiguous|mismatch|blocked)$/.test(b))) return {ok:false,attempt,readiness};
      if (attempt < attempts) await sleepMs(delayMs);
    }
    return {ok:false,attempt:attempts,readiness};
  }

  function resolveBookingSubmitSettleMs(settings, autoOpenedFromDoctor) {
    if (!autoOpenedFromDoctor) {
      return 0;
    }
    return pickDelayMs(
      settings?.pacing?.bookingSubmitSettleMs ??
        CONFIG_DEFAULTS.pacing.bookingSubmitSettleMs,
    );
  }

  async function waitForBookingSubmitSettle(settings, autoOpenedFromDoctor) {
    const delayMs = resolveBookingSubmitSettleMs(settings, autoOpenedFromDoctor);
    if (delayMs <= 0) {
      return { waited: false, delayMs: 0 };
    }
    setSummary("info", "Waiting for booking page initialization before submit.", {
      delayMs,
      source: "doctor-page-auto-open",
    });
    await sleepMs(delayMs);
    return { waited: true, delayMs };
  }

  function isVisible(element) {
    if (!element) {
      return false;
    }
    const style = globalThis.getComputedStyle(element);
    return style.display !== "none" && style.visibility !== "hidden";
  }

  function findSubmitControl() {
    const selector = SUBMIT_SELECTOR;
    const candidates = Array.from(document.querySelectorAll(selector));
    if (candidates.length !== 1 || !isVisible(candidates[0]) || candidates[0].disabled || typeof candidates[0].click !== "function" || (candidates[0].getClientRects && !formActionable(candidates[0]))) return { method: "not-found", target: null };
    return { method: "selector", target: selector, element: candidates[0] };
  }

  function triggerSubmitControl(control = findSubmitControl()) {
    if (control.method !== "selector") return { method: "not-found", target: null };
    return { method: control.method, target: control.target, activation: activateSubmitElement(control.element) };
  }

  function activateSubmitElement(element) {
    if (!element) {
      return { method: "none" };
    }
    element.scrollIntoView?.({ block: "center", inline: "center" });
    element.focus?.({ preventScroll: true });
    if (typeof element.click === "function") {
      element.click();
      return { method: "native-click" };
    }
    throw new Error("manual control required");
  }

  async function markSubmitInProgress(formState, memberSelection, _fillResult, _attemptCount, target = readState().lastTarget, guard = () => true) {
    const record = await beginAttempt([target?.unitId, target?.depId, target?.doctorId, memberSelection.memberId, formState.scheduleId, formState.appointmentValue], guard);
    activeControllerId = null;
    patchState((next) => ({ ...next, running: false, controllerId: null, pendingBooking: null, submittingBooking: record }));
    return record;
  }

  async function submitTransaction(control, formState, memberSelection, target, authorized = false, evidenceAdapter = null, guard = () => true) {
    if (submissionBlocked()) return "OUTCOME_UNKNOWN";
    if (!authorized || control.method !== "selector") return "AWAITING_MANUAL_CONFIRMATION";
    let record;
    try { record = await markSubmitInProgress(formState, memberSelection, null, 1, target, guard); }
    catch (error) { return error.message === "cancelled" ? "AWAITING_MANUAL_CONFIRMATION" : "OUTCOME_UNKNOWN"; }
    let outcome = "OUTCOME_UNKNOWN";
    try {
      triggerSubmitControl(control);
      // No live evidence/follow-up adapter is verified. Unknown controls, terms,
      // security checks and payment remain manual. Never repeat a click.
      if (evidenceAdapter) {
        const evidence = await evidenceAdapter();
        const expected = [target.unitId, target.depId, target.doctorId, memberSelection.memberId, formState.scheduleId, formState.appointmentValue];
        if (evidence && TERMINAL.has(evidence.state) && JSON.stringify(evidence.selection) === JSON.stringify(expected)) outcome = evidence.state;
      }
    } catch (_error) { outcome = "OUTCOME_UNKNOWN"; }
    try { finishAttempt(record.attempt_id, outcome); }
    catch (_error) { outcome = "OUTCOME_UNKNOWN"; }
    return outcome;
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
      success: false,
      state: "OUTCOME_UNKNOWN",
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
      outcome: submissionBlocked() ? "OUTCOME_UNKNOWN" : reason === "Stopped." ? state.outcome : "AWAITING_MANUAL_CONFIRMATION",
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
      stopRun("Session recovery disabled; manual login required.");
      return false;
    }
    const state = readState();
    if (state.sessionRecoveryAttempts >= settings.session.recoveryMaxAttempts) {
      stopRun("Login expired; manual login required.");
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
      stopRun("Doctor target resolution failed.");
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
            date: nextSlot.date,
          },
        }));
        setSummary(
          "info",
          "Matched slot; opening booking page.",
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
    const autoOpenedFromDoctor = Boolean(state.pendingBooking?.scheduleId);
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
      const failures = Number(state.preSubmitFailures ?? 0) + 1;
      patchState((next) => ({ ...next, preSubmitFailures: failures }));
      if (failures >= settings.booking.maxPreSubmitAttempts) {
        stopRun("Booking form preparation failed; manual action required.");
        return;
      }
      await returnToDoctorAfterCurrentAttempt(
        target,
        "Booking page hit rate limiting.",
        settings.pacing.rateLimitCooldownMs,
      );
      return;
    }

    const memberSelection = resolveMemberSelection(settings.member);
    if (!memberSelection.ok) {
      stopRun("Member selection failed.");
      return;
    }

    const formState = parseBookingFormState(settings.filters, bookingTarget.scheduleId);
    if (!formState.isValid) {
      stopRun("Booking form invalid.");
      return;
    }

    formState.expectedDate = state.pendingBooking?.date ?? null;
    if (formState.scheduleId !== bookingTarget.scheduleId) { stopRun("Booking form invalid."); return; }
    const preparation = await prepareBookingFormForSubmit(
      formState,
      memberSelection,
      settings.address,
      settings.booking,
    );
    if (!preparation.ok) {
      stopRun("Booking form preparation failed; manual action required.");
      return;
    }
    let authorized = false;
    try {
      authorized = settings.booking.submitMode === "auto" && await ensureSubmissionConsent(target, memberSelection, { interactive: readState().interactiveConsent === true });
    } catch (_error) {
      stopRun("Submission outcome unknown; verify original site records.");
      return;
    }
    if (!authorized) {
      patchState((next) => ({
        ...next,
        running: false,
        pendingBooking: null,
        submittingBooking: null,
        outcome: "AWAITING_MANUAL_CONFIRMATION",
      }));
      setSummary(
        "info",
        "Booking form prepared; waiting for manual submit.",
        {
          scheduleId: formState.scheduleId,
          appointmentValue: formState.appointmentValue,
          appointmentLabel: formState.appointmentLabel,
          memberId: memberSelection.memberId,
          ready: true,
        },
      );
      renderPanel();
      return;
    }

    const settle = await waitForBookingSubmitSettle(settings, autoOpenedFromDoctor);
    if (settle.waited) {
      appendLog("debug", "Waited for booking page initialization before submit.", settle);
    }
    await sleepMs(pickDelayMs(settings.pacing.pageActionMs));
    // Stop/restart during an awaited prepare/settle revokes this controller.
    if (!isControllerActive(controllerId)) return;
    const submitControl = findSubmitControl();
    if (submitControl.method === "not-found") {
      stopRun("Could not find a submit control on the booking page.");
      return;
    }
    const outcome = await submitTransaction(submitControl, formState, memberSelection, target, authorized, null, () => isControllerActive(controllerId) && consentStillValid() && readSettings().booking.submitMode === "auto" && readBookingFormReadiness(formState, memberSelection, settings.address, settings.booking).ok && findSubmitControl().element === submitControl.element);
    patchState((next) => ({ ...next, running: false, outcome }));
    setSummary("warn", outcome === "OUTCOME_UNKNOWN" ? "Submission outcome unknown; verify original site records." : "Booking form prepared; waiting for manual submit.");
    renderPanel();
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
      <div class="grab160-title">
        <div>
          <div class="grab160-title-main">挂号值守</div>
          <div class="grab160-title-sub">160Grab v${SCRIPT_VERSION}</div>
        </div>
        <span class="grab160-drag-hint">drag</span>
      </div>
      <div class="grab160-body"></div>
    `;
    Object.assign(panel.style, {
      position: "fixed",
      zIndex: "999999",
      width: "420px",
      maxWidth: "calc(100vw - 24px)",
      maxHeight: "86vh",
      overflow: "auto",
      padding: "14px",
      borderRadius: "10px",
      boxShadow:
        "0 0 0 1px rgba(188, 224, 217, 0.12), 0 18px 42px rgba(2, 12, 18, 0.36)",
      background: "#101d23",
      color: "#eef7f5",
      fontSize: "13px",
      lineHeight: "1.5",
      fontFamily:
        "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif",
    });
    const style = document.createElement("style");
    style.textContent = `
      #${PANEL_ID}, #${PANEL_ID} * {
        box-sizing: border-box;
        -webkit-font-smoothing: antialiased;
      }
      #${PANEL_ID} {
        scrollbar-color: rgba(116, 211, 197, 0.36) transparent;
      }
      #${PANEL_ID} button,
      #${PANEL_ID} input,
      #${PANEL_ID} select {
        font: inherit;
      }
      #${PANEL_ID} .grab160-title {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        padding: 2px 2px 12px;
        user-select: none;
      }
      #${PANEL_ID} .grab160-title-main {
        color: #f5fffc;
        font-size: 17px;
        font-weight: 750;
        line-height: 1.15;
        letter-spacing: 0;
      }
      #${PANEL_ID} .grab160-title-sub {
        margin-top: 2px;
        color: #8ea7a5;
        font-size: 11px;
        font-weight: 600;
      }
      #${PANEL_ID} .grab160-drag-hint {
        border: 1px solid rgba(188, 224, 217, 0.12);
        border-radius: 999px;
        padding: 3px 8px;
        color: #8ea7a5;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: .08em;
        text-transform: uppercase;
      }
      #${PANEL_ID} .grab160-body {
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      #${PANEL_ID} button {
        appearance: none;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
        min-height: 40px;
        border: 1px solid rgba(188, 224, 217, 0.16);
        background: rgba(188, 224, 217, 0.07);
        color: #eef7f5;
        border-radius: 7px;
        padding: 7px 10px;
        cursor: pointer;
        font-weight: 650;
        transition:
          background-color 140ms cubic-bezier(0.23, 1, 0.32, 1),
          border-color 140ms cubic-bezier(0.23, 1, 0.32, 1),
          color 140ms cubic-bezier(0.23, 1, 0.32, 1),
          transform 120ms cubic-bezier(0.23, 1, 0.32, 1);
      }
      #${PANEL_ID} button:hover {
        border-color: rgba(116, 211, 197, 0.38);
        background: rgba(116, 211, 197, 0.14);
      }
      #${PANEL_ID} button:active {
        transform: scale(0.98);
      }
      #${PANEL_ID} button:focus-visible,
      #${PANEL_ID} input:focus-visible,
      #${PANEL_ID} select:focus-visible,
      #${PANEL_ID} summary:focus-visible,
      #${PANEL_ID} .grab160-help:focus-visible {
        outline: 2px solid rgba(116, 211, 197, 0.72);
        outline-offset: 2px;
      }
      #${PANEL_ID} .grab160-primary-button {
        background: #4fd1bd;
        border-color: #80e4d4;
        color: #062521;
        box-shadow: 0 1px 0 rgba(255, 255, 255, 0.18) inset;
      }
      #${PANEL_ID} .grab160-primary-button:hover {
        background: #6de0ce;
        border-color: #9af2de;
      }
      #${PANEL_ID} .grab160-danger-button {
        border-color: rgba(255, 138, 138, 0.32);
        color: #ffd6d6;
      }
      #${PANEL_ID} .grab160-danger-button:hover {
        background: rgba(255, 138, 138, 0.12);
        border-color: rgba(255, 138, 138, 0.52);
      }
      #${PANEL_ID} input,
      #${PANEL_ID} select {
        width: 100%;
        min-height: 40px;
        border: 1px solid rgba(188, 224, 217, 0.16);
        border-radius: 7px;
        padding: 7px 9px;
        background: #17272e;
        color: #f5fffc;
        box-shadow: 0 1px 0 rgba(255, 255, 255, 0.03) inset;
      }
      #${PANEL_ID} input::placeholder {
        color: #6f8583;
      }
      #${PANEL_ID} input[type="checkbox"] {
        width: 16px;
        min-width: 16px;
        height: 16px;
        min-height: 16px;
        padding: 0;
        accent-color: #4fd1bd;
      }
      #${PANEL_ID} label {
        color: #cfe0dd;
      }
      #${PANEL_ID} .grab160-row {
        display: flex;
        gap: 8px;
        align-items: center;
      }
      #${PANEL_ID} .grab160-row > * { flex: 1; min-width: 0; }
      #${PANEL_ID} .grab160-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
      }
      #${PANEL_ID} .grab160-grid-3 {
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }
      #${PANEL_ID} .grab160-grid-1 {
        grid-template-columns: 1fr;
      }
      #${PANEL_ID} .grab160-buttons {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }
      #${PANEL_ID} .grab160-buttons button {
        flex: 1 1 auto;
      }
      #${PANEL_ID} .grab160-actionbar {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 8px;
      }
      #${PANEL_ID} .grab160-actionbar button {
        padding-left: 8px;
        padding-right: 8px;
      }
      #${PANEL_ID} .grab160-watch {
        border: 1px solid rgba(188, 224, 217, 0.12);
        border-radius: 9px;
        background: #13242b;
        padding: 11px;
      }
      #${PANEL_ID} .grab160-stage-rail {
        display: grid;
        grid-template-columns: repeat(6, minmax(0, 1fr));
        gap: 5px;
        margin-bottom: 10px;
      }
      #${PANEL_ID} .grab160-stage {
        min-width: 0;
        border-radius: 999px;
        padding: 4px 5px;
        background: rgba(188, 224, 217, 0.06);
        color: #78918e;
        font-size: 10px;
        font-weight: 700;
        text-align: center;
        white-space: nowrap;
      }
      #${PANEL_ID} .grab160-stage-active {
        background: rgba(79, 209, 189, 0.18);
        color: #9af2de;
        box-shadow: 0 0 0 1px rgba(79, 209, 189, 0.28) inset;
      }
      #${PANEL_ID} .grab160-watch-grid {
        display: grid;
        grid-template-columns: 1.15fr .85fr .85fr;
        gap: 8px;
      }
      #${PANEL_ID} .grab160-watch-card {
        min-width: 0;
        border: 1px solid rgba(188, 224, 217, 0.10);
        border-radius: 8px;
        background: rgba(188, 224, 217, 0.05);
        padding: 8px;
      }
      #${PANEL_ID} .grab160-watch-card span,
      #${PANEL_ID} .grab160-field-note {
        display: block;
        color: #8ea7a5;
        font-size: 11px;
        font-weight: 600;
      }
      #${PANEL_ID} .grab160-watch-card strong {
        display: block;
        margin-top: 2px;
        color: #f5fffc;
        font-size: 18px;
        font-variant-numeric: tabular-nums;
        line-height: 1.2;
      }
      #${PANEL_ID} .grab160-watch-card-primary strong {
        color: #9af2de;
        font-size: 22px;
      }
      #${PANEL_ID} .grab160-summary {
        margin-top: 10px;
        display: grid;
        gap: 3px;
      }
      #${PANEL_ID} .grab160-muted { color: #8ea7a5; }
      #${PANEL_ID} .grab160-danger-text { color: #ffb4b4 !important; }
      #${PANEL_ID} .grab160-status {
        display: block;
        position: static;
        width: auto;
        height: auto;
        padding: 0;
        margin: 0;
        line-height: inherit;
        pointer-events: none;
        font-weight: 700;
      }
      #${PANEL_ID} .grab160-status-warn { color: #f6c453; }
      #${PANEL_ID} .grab160-status-error { color: #ff8a8a; }
      #${PANEL_ID} .grab160-status-success { color: #7de3a6; }
      #${PANEL_ID} .grab160-section {
        border: 1px solid rgba(188, 224, 217, 0.12);
        border-radius: 9px;
        background: rgba(188, 224, 217, 0.04);
        overflow: hidden;
      }
      #${PANEL_ID} .grab160-section + .grab160-section {
        margin-top: 10px;
      }
      #${PANEL_ID} summary {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        min-height: 40px;
        padding: 9px 11px;
        color: #f5fffc;
        cursor: pointer;
        font-weight: 750;
        list-style: none;
      }
      #${PANEL_ID} summary::-webkit-details-marker {
        display: none;
      }
      #${PANEL_ID} summary::after {
        content: "+";
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 20px;
        height: 20px;
        border-radius: 999px;
        background: rgba(188, 224, 217, 0.08);
        color: #8ea7a5;
        font-weight: 800;
      }
      #${PANEL_ID} details[open] summary::after {
        content: "-";
      }
      #${PANEL_ID} .grab160-section-body {
        display: grid;
        gap: 10px;
        padding: 0 11px 11px;
      }
      #${PANEL_ID} .grab160-field {
        display: grid;
        gap: 5px;
      }
      #${PANEL_ID} .grab160-label-text {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        color: #cfe0dd;
        font-size: 12px;
        font-weight: 700;
      }
      #${PANEL_ID} .grab160-help {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 17px;
        height: 17px;
        border-radius: 999px;
        border: 1px solid rgba(116, 211, 197, 0.32);
        color: #9af2de;
        font-size: 11px;
        font-weight: 850;
        cursor: help;
      }
      #${PANEL_TOOLTIP_ID} {
        position: fixed;
        z-index: 1000000;
        display: none;
        max-width: min(300px, calc(100vw - 24px));
        border: 1px solid rgba(116, 211, 197, 0.42);
        border-radius: 8px;
        background: #f5fffc;
        color: #0d2328;
        box-shadow:
          0 0 0 1px rgba(2, 12, 18, 0.06),
          0 14px 30px rgba(2, 12, 18, 0.28);
        padding: 8px 10px;
        font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        font-size: 12px;
        font-weight: 650;
        line-height: 1.45;
        pointer-events: none;
        opacity: 0;
        transform: translateY(2px);
        transition:
          opacity 120ms cubic-bezier(0.23, 1, 0.32, 1),
          transform 120ms cubic-bezier(0.23, 1, 0.32, 1);
      }
      #${PANEL_TOOLTIP_ID}.grab160-tooltip-visible {
        opacity: 1;
        transform: translateY(0);
      }
      #${PANEL_ID} .grab160-check {
        display: flex;
        align-items: center;
        min-height: 40px;
        gap: 8px;
        border: 1px solid rgba(188, 224, 217, 0.10);
        border-radius: 8px;
        background: rgba(188, 224, 217, 0.04);
        padding: 7px 9px;
      }
      #${PANEL_ID} .grab160-chip-row {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }
      #${PANEL_ID} .grab160-chip {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        min-height: 40px;
        border: 1px solid rgba(188, 224, 217, 0.10);
        border-radius: 999px;
        background: rgba(188, 224, 217, 0.04);
        padding: 5px 9px;
        white-space: nowrap;
      }
      #${PANEL_ID} .grab160-hour-list {
        display: grid;
        gap: 7px;
      }
      #${PANEL_ID} .grab160-hour-row {
        display: grid;
        grid-template-columns: 1fr 1fr auto;
        gap: 8px;
        align-items: center;
      }
      #${PANEL_ID} .grab160-range-field .grab160-row {
        gap: 8px;
      }
      #${PANEL_ID} .grab160-warning-box {
        border: 1px solid rgba(246, 196, 83, 0.24);
        border-radius: 8px;
        background: rgba(246, 196, 83, 0.08);
        color: #f8df9b;
        padding: 8px 9px;
        font-size: 12px;
      }
      #${PANEL_ID} .grab160-log {
        white-space: pre-wrap;
        border: 1px solid rgba(188, 224, 217, 0.12);
        border-radius: 8px;
        background: #0d181d;
        color: #bdd7d3;
        padding: 9px;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        font-size: 11px;
        line-height: 1.55;
      }
      @media (max-width: 520px) {
        #${PANEL_ID} {
          width: calc(100vw - 24px) !important;
        }
        #${PANEL_ID} .grab160-actionbar,
        #${PANEL_ID} .grab160-watch-grid,
        #${PANEL_ID} .grab160-grid,
        #${PANEL_ID} .grab160-grid-3 {
          grid-template-columns: 1fr;
        }
      }
      @media (prefers-reduced-motion: reduce) {
        #${PANEL_ID} button {
          transition: none;
        }
        #${PANEL_ID} button:active {
          transform: none;
        }
      }
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

  function renderHelp(text) {
    return `<span class="grab160-help" data-help="${htmlEscape(text)}" aria-label="${htmlEscape(
      text,
    )}" tabindex="0">?</span>`;
  }

  function ensurePanelTooltip() {
    let tooltip = document.getElementById(PANEL_TOOLTIP_ID);
    if (tooltip) {
      return tooltip;
    }
    tooltip = document.createElement("div");
    tooltip.id = PANEL_TOOLTIP_ID;
    tooltip.setAttribute("role", "tooltip");
    document.documentElement.appendChild(tooltip);
    return tooltip;
  }

  function positionPanelTooltip(trigger, tooltip) {
    const margin = 10;
    const gap = 10;
    const triggerRect = trigger.getBoundingClientRect();
    const tooltipWidth = tooltip.offsetWidth || 260;
    const tooltipHeight = tooltip.offsetHeight || 40;
    let left = triggerRect.right + gap;
    if (left + tooltipWidth + margin > globalThis.innerWidth) {
      left = triggerRect.left - tooltipWidth - gap;
    }
    left = Math.max(margin, Math.min(left, globalThis.innerWidth - tooltipWidth - margin));
    const centeredTop = triggerRect.top + triggerRect.height / 2 - tooltipHeight / 2;
    const top = Math.max(
      margin,
      Math.min(centeredTop, globalThis.innerHeight - tooltipHeight - margin),
    );
    tooltip.style.left = `${Math.round(left)}px`;
    tooltip.style.top = `${Math.round(top)}px`;
  }

  function showPanelTooltip(trigger) {
    const text = trigger.getAttribute("data-help");
    if (!text) {
      return;
    }
    const tooltip = ensurePanelTooltip();
    tooltip.textContent = text;
    tooltip.style.display = "block";
    trigger.setAttribute("aria-describedby", PANEL_TOOLTIP_ID);
    positionPanelTooltip(trigger, tooltip);
    globalThis.requestAnimationFrame?.(() => {
      tooltip.classList.add("grab160-tooltip-visible");
    });
    if (!globalThis.requestAnimationFrame) {
      tooltip.classList.add("grab160-tooltip-visible");
    }
  }

  function hidePanelTooltip(trigger = null) {
    const tooltip = document.getElementById(PANEL_TOOLTIP_ID);
    if (!tooltip?.classList || !tooltip?.style) {
      return;
    }
    tooltip.classList.remove("grab160-tooltip-visible");
    tooltip.style.display = "none";
    trigger?.removeAttribute("aria-describedby");
  }

  function installPanelTooltips(container) {
    if (!container?.querySelectorAll) {
      return;
    }
    container.querySelectorAll(".grab160-help").forEach((trigger) => {
      if (trigger.dataset.tooltipInstalled === "1") {
        return;
      }
      trigger.dataset.tooltipInstalled = "1";
      trigger.addEventListener("pointerenter", () => showPanelTooltip(trigger));
      trigger.addEventListener("pointermove", () => {
        const tooltip = document.getElementById(PANEL_TOOLTIP_ID);
        if (tooltip?.classList.contains("grab160-tooltip-visible")) {
          positionPanelTooltip(trigger, tooltip);
        }
      });
      trigger.addEventListener("pointerleave", () => hidePanelTooltip(trigger));
      trigger.addEventListener("focus", () => showPanelTooltip(trigger));
      trigger.addEventListener("blur", () => hidePanelTooltip(trigger));
      trigger.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          hidePanelTooltip(trigger);
          trigger.blur();
        }
      });
    });
  }

  function renderLabelText(label, helpText = "") {
    return `<span class="grab160-label-text"><span>${htmlEscape(label)}</span>${
      helpText ? renderHelp(helpText) : ""
    }</span>`;
  }

  function renderStageRail(activePhase) {
    return ["idle", "polling", "hit", "submit", "cooldown", "error"]
      .map(
        (phase) =>
          `<span class="grab160-stage ${
            phase === activePhase ? "grab160-stage-active" : ""
          }" data-panel-stage="${phase}">${panelPhaseLabel(phase)}</span>`,
      )
      .join("");
  }

  function renderField(label, helpText, inputHtml) {
    return `<label class="grab160-field">${renderLabelText(label, helpText)}${inputHtml}</label>`;
  }

  function renderCheck(path, label, checked, helpText) {
    return `<label class="grab160-check"><input data-setting="${path}" type="checkbox" ${
      checked ? "checked" : ""
    }><span>${htmlEscape(label)}</span>${renderHelp(helpText)}</label>`;
  }

  function renderPanel() {
    const panel = createPanel();
    const body = panel.querySelector(".grab160-body");
    const state = readState();
    const settings = readSettings();
    const summary = state.summary;
    const phase = panelPhase(state, summary);
    const autoSubmitText = settings.booking.autoSubmit ? "ON" : "off";
    hidePanelTooltip();
    body.innerHTML = `
      <div class="grab160-watch">
        <div class="grab160-stage-rail">${renderStageRail(phase)}</div>
        <div class="grab160-watch-grid">
          <div class="grab160-watch-card grab160-watch-card-primary">
            <span>阶段</span>
            <strong data-panel-phase-label>${panelPhaseLabel(phase)}</strong>
          </div>
          <div class="grab160-watch-card">
            <span>医生目标</span>
            <strong data-panel-target>${htmlEscape(panelDoctorText(state))}</strong>
          </div>
          <div class="grab160-watch-card">
            <span>轮询次数</span>
            <strong data-panel-attempts>${state.pollAttempt}</strong>
          </div>
          <div class="grab160-watch-card">
            <span>提交状态</span>
            <strong data-panel-running>${htmlEscape(state.outcome ?? "DISCOVERED")}</strong>
          </div>
          <div class="grab160-watch-card">
            <span>自动提交</span>
            <strong class="${settings.booking.autoSubmit ? "grab160-danger-text" : ""}" data-panel-auto-submit>${autoSubmitText}</strong>
          </div>
          <div class="grab160-watch-card">
            <span>会话恢复</span>
            <strong data-panel-recovery-attempts>${state.sessionRecoveryAttempts}/${settings.session.recoveryMaxAttempts}</strong>
          </div>
        </div>
        <div class="grab160-summary">
          <span class="grab160-status ${statusClassName(summary)}" data-panel-summary-message>${htmlEscape(summary?.message ?? "Ready")}</span>
          <span class="grab160-muted" data-panel-summary-detail>${htmlEscape(formatPanelDetail(summary?.detail))}</span>
        </div>
      </div>
      <div class="grab160-buttons grab160-actionbar">
        <button class="grab160-primary-button" type="button" data-action="start">${state.running ? "Restart" : "Start"}</button>
        <button class="grab160-danger-button" type="button" data-action="stop">Stop</button>
        <button type="button" data-action="main">Overview</button>
        <button type="button" data-action="settings">Settings</button>
        <button type="button" data-action="logs">Logs</button>
        <button type="button" data-action="reset-state">Reset State</button>
        <button type="button" data-action="revoke-consent">撤销授权</button>
        <button type="button" data-action="resolve-booked">核对：已预约</button>
        <button type="button" data-action="resolve-not-booked">核对：未预约</button>
      </div>
      ${state.activeView === "settings" ? renderSettingsView(settings) : ""}
      ${state.activeView === "logs" ? renderLogsView(state, settings) : ""}
    `;
    body.querySelector('[data-action="revoke-consent"]')?.addEventListener("click", revokeConsent);
    body.querySelector('[data-action="resolve-booked"]')?.addEventListener("click", () => resolvePending(true));
    body.querySelector('[data-action="resolve-not-booked"]')?.addEventListener("click", () => resolvePending(false));
    body.querySelector('[data-action="start"]')?.addEventListener("click", startRun);
    body.querySelector('[data-action="stop"]')?.addEventListener("click", () => stopRun());
    body.querySelector('[data-action="main"]')?.addEventListener("click", () => setActiveView("main"));
    body
      .querySelector('[data-action="settings"]')
      ?.addEventListener("click", () => setActiveView("settings"));
    body.querySelector('[data-action="logs"]')?.addEventListener("click", () => setActiveView("logs"));
    body
      .querySelector('[data-action="reset-state"]')
      ?.addEventListener("click", resetRuntimeState);
    installPanelTooltips(body);
    if (state.activeView === "settings") {
      wireSettingsView(body, settings);
    }
  }

  function renderSettingsView(settings) {
    const hourRows =
      settings.filters.hours.length > 0 ? settings.filters.hours : ["08:00-09:00"];
    const dayLabels = {
      am: "上午 am",
      pm: "下午 pm",
      em: "夜间 em",
    };
    return `
      <div data-settings-view>
        <details class="grab160-section" open>
          <summary><span>就诊人与地址</span><span class="grab160-field-note">预约页填表使用</span></summary>
          <div class="grab160-section-body">
            <div class="grab160-grid">
              ${renderField(
                "Member ID",
                "指定就诊人 ID。为空时，如果预约页只有一个明确就诊人，脚本会自动选择；多个候选时需要填 ID 或标签。",
                `<input data-setting="member.memberId" type="password" value="${htmlEscape(
                  settings.member.memberId ?? "",
                )}">`,
              )}
              ${renderField(
                "Member Label",
                "按就诊人显示文字精确匹配；多个候选时须填写 memberId。",
                `<input data-setting="member.memberLabel" value="${htmlEscape(
                  settings.member.memberLabel ?? "",
                )}">`,
              )}
            </div>
            ${renderCheck(
              "member.show",
              "Show member ID",
              false,
              "只临时显示上方 Member ID 明文，不会保存为配置。",
            )}
            <div class="grab160-grid grab160-grid-3">
              ${renderField(
                "Province",
                "填写完整省份名称或地区 ID；已有值或成员资料与配置冲突时交人工。",
                `<input data-setting="address.province" value="${htmlEscape(
                  settings.address.province ?? "",
                )}">`,
              )}
              ${renderField(
                "City",
                "填写完整城市名称或地区 ID；留空保留站点或成员资料已有值。",
                `<input data-setting="address.city" value="${htmlEscape(
                  settings.address.city ?? "",
                )}">`,
              )}
              ${renderField(
                "Area",
                "填写完整区县名称或地区 ID；留空保留站点或成员资料已有值。",
                `<input data-setting="address.area" value="${htmlEscape(
                  settings.address.area ?? "",
                )}">`,
              )}
            </div>
            ${renderField(
              "Address Detail",
              "预约页要求填写详细地址时使用。若就诊人资料已有详细地址，会优先复用资料值。",
              `<input data-setting="address.detail" value="${htmlEscape(
                settings.address.detail ?? "",
              )}">`,
            )}
          </div>
        </details>

        <details class="grab160-section" open>
          <summary><span>筛选时间</span><span class="grab160-field-note">决定刷哪些号源</span></summary>
          <div class="grab160-section-body">
            <div class="grab160-grid">
              ${renderField(
                "Start At",
                "到这个时间才开始轮询；为空则点击 Start 后立即开始。",
                `<input data-setting="runtime.startAt" type="datetime-local" value="${htmlEscape(
                  settings.runtime.startAt ?? "",
                )}">`,
              )}
              ${renderField(
                "Appointment From",
                "从哪一天开始查询号源，不是脚本启动时间；为空则从今天开始。",
                `<input data-setting="filters.startDate" type="date" value="${htmlEscape(
                  settings.filters.startDate ?? "",
                )}">`,
              )}
            </div>
            <div class="grab160-field">
              ${renderLabelText("Weekdays", "只提交选中星期的号源；全不选表示星期不限。")}
              <div class="grab160-chip-row">${[1, 2, 3, 4, 5, 6, 7]
                .map(
                  (day) =>
                    `<label class="grab160-chip"><input data-week="${day}" type="checkbox" ${
                      settings.filters.weeks.includes(day) ? "checked" : ""
                    }>周${"一二三四五六日"[day - 1]}</label>`,
                )
                .join("")}</div>
            </div>
            <div class="grab160-field">
              ${renderLabelText("Periods", "只提交选中时段的号源；全不选表示上午/下午/夜间不限。")}
              <div class="grab160-chip-row">${["am", "pm", "em"]
                .map(
                  (day) =>
                    `<label class="grab160-chip"><input data-period="${day}" type="checkbox" ${
                      settings.filters.days.includes(day) ? "checked" : ""
                    }>${dayLabels[day]}</label>`,
                )
                .join("")}</div>
            </div>
            <div class="grab160-field">
              ${renderLabelText(
                "Hours",
                "只提交落在这些具体时间范围内的号源，固定半小时粒度；为空表示具体时间不限。",
              )}
              <div class="grab160-hour-list" data-hours>${hourRows
                .map((range) => {
                  const [start, end] = range.split("-");
                  return `<div class="grab160-hour-row" data-hour-row><input type="time" step="1800" value="${htmlEscape(
                    start,
                  )}"><input type="time" step="1800" value="${htmlEscape(
                    end,
                  )}"><button type="button" data-remove-hour>Delete</button></div>`;
                })
                .join("")}</div>
              <button type="button" data-add-hour>Add Time Range</button>
            </div>
          </div>
        </details>

        <details class="grab160-section" open>
          <summary><span>自动提交</span><span class="grab160-field-note">提交前最后一道开关</span></summary>
          <div class="grab160-section-body">
            <div class="grab160-warning-box">Auto Submit 开启后，脚本会在预约页准备完成时点击最终提交按钮；首次 smoke 建议保持关闭。</div>
            ${renderField("Submit mode", "自动模式仍须明确授权；人工模式只准备表单。", `<select data-setting="booking.submitMode"><option value="auto" ${settings.booking.submitMode === "auto" ? "selected" : ""}>Auto</option><option value="manual_confirm" ${settings.booking.submitMode === "manual_confirm" ? "selected" : ""}>Manual confirm</option></select>`)}
            ${renderCheck(
              "booking.autoReturnAfterSubmitFailure",
              "Auto return after submit failure",
              settings.booking.autoReturnAfterSubmitFailure,
              "未知结果始终停下；此选项不能解除未决提交。",
            )}
            ${renderField(
              "Disease description",
              "仅填写真实病情；为空时保留站点已有值，冲突或缺必填值交人工。",
              `<input data-setting="booking.diseaseDescription" value="${htmlEscape(
                settings.booking.diseaseDescription ?? "",
              )}">`,
            )}
            ${renderField(
              "Clinic card",
              "仅使用真实就诊卡；留空等待站点查询，证件号不会被复制为卡号。",
              `<input data-setting="booking.clinicCard" type="password" value="${htmlEscape(settings.booking.clinicCard ?? "")}">`,
            )}
            ${renderField(
              "Max pre-submit attempts",
              "仅提交前准备重试，最多三次；进入提交边界后不再重试。",
              `<input data-setting="booking.maxPreSubmitAttempts" type="number" min="1" max="3" value="${settings.booking.maxPreSubmitAttempts}">`,
            )}
          </div>
        </details>

        <details class="grab160-section">
          <summary><span>Advanced</span><span class="grab160-field-note">节流、恢复、日志</span></summary>
          <div class="grab160-section-body">
            <div class="grab160-grid">
              ${renderRangeInputs(
                "轮询间隔 ms",
                "pacing.pollMs",
                settings.pacing.pollMs,
                3000,
                "两次查询排班之间的随机等待；越小越快，也越容易触发访问频繁。",
              )}
              ${renderRangeInputs(
                "页面动作 ms",
                "pacing.pageActionMs",
                settings.pacing.pageActionMs,
                0,
                "打开预约页、选择表单、点击按钮前后的随机等待，模拟人工操作节奏。",
              )}
              ${renderRangeInputs(
                "提交稳定 ms",
                "pacing.bookingSubmitSettleMs",
                settings.pacing.bookingSubmitSettleMs,
                500,
                "提交预约前等待预约页初始化稳定的时间，只在医生页自动跳转到预约页后生效。",
              )}
              ${renderRangeInputs(
                "失败重试 ms",
                "pacing.bookingRetryMs",
                settings.pacing.bookingRetryMs,
                1000,
                "同一个号源提交失败后，再次尝试前的随机等待。",
              )}
              ${renderRangeInputs(
                "限频冷却 ms",
                "pacing.rateLimitCooldownMs",
                settings.pacing.rateLimitCooldownMs,
                15000,
                "检测到访问频繁后暂停多久再继续。",
              )}
            </div>
            ${renderCheck(
              "session.recoveryEnabled",
              "Session Recovery",
              settings.session.recoveryEnabled,
              "登录态或会话 key 丢失时，是否尝试刷新医生页并恢复轮询。",
            )}
            <div class="grab160-grid">
              ${renderField(
                "Keepalive seconds",
                "轮询期间多久后台访问一次就诊人页面，尽量维持登录态；设为 0 可关闭。",
                `<input data-setting="session.keepAliveIntervalSeconds" type="number" min="0" value="${settings.session.keepAliveIntervalSeconds}">`,
              )}
              ${renderField(
                "Recovery max attempts",
                "登录态丢失后最多允许几次恢复尝试，超过后停止并提示人工处理。",
                `<input data-setting="session.recoveryMaxAttempts" type="number" min="0" value="${settings.session.recoveryMaxAttempts}">`,
              )}
              ${renderField(
                "Log Level",
                "控制面板和控制台保留的最低日志级别；debug 最详细，error 最安静。",
                `<select data-setting="logging.level">${LOG_LEVELS.map(
                  (level) =>
                    `<option value="${level}" ${
                      level === settings.logging.level ? "selected" : ""
                    }>${level}</option>`,
                ).join("")}</select>`,
              )}
            </div>
          </div>
        </details>
        <div class="grab160-buttons">
          <button type="button" data-save-settings>Save Settings</button>
          <button type="button" data-reset-settings>Reset Settings</button>
        </div>
      </div>
    `;
  }

  function renderRangeInputs(label, path, value, min = 0, helpText = "") {
    return `<label class="grab160-field grab160-range-field">${renderLabelText(
      label,
      helpText,
    )}<span class="grab160-row"><input data-range="${path}" data-range-index="0" type="number" min="${min}" value="${value[0]}" aria-label="${htmlEscape(
      `${label} min`,
    )}"><input data-range="${path}" data-range-index="1" type="number" min="${min}" value="${
      value[1]
    }" aria-label="${htmlEscape(`${label} max`)}"></span></label>`;
  }

  function renderLogsView(state, settings) {
    const logs = (state.logs ?? []).filter((entry) => shouldLog(entry.level, settings));
    return `<div class="grab160-log">${htmlEscape(
      logs.length
        ? logs
            .map(
              (entry) =>
                `[${entry.ts}] ${entry.level.toUpperCase()} ${entry.message} ${entry.detail}`,
            )
            .join("\n")
        : "暂无日志",
    )}</div>`;
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
      "booking.submitMode",
      "booking.autoReturnAfterSubmitFailure",
      "booking.diseaseDescription",
      "booking.clinicCard",
      "booking.maxPreSubmitAttempts",
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
      "pacing.bookingSubmitSettleMs",
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
      row.className = "grab160-hour-row";
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
        stopRun("Userscript failed; manual action required.");
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
        stopRun("Userscript failed; manual action required.");
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
    runBookingPageController, ensureSubmissionConsent, revokeConsent, writeSettings, readSettings,
    JOURNAL_KEY, readJournal, writeJournal, submissionBlocked, beginAttempt, finishAttempt, submitTransaction, resolvePending, startRun, stopRun, resetRuntimeState,
    appendLog,
    setSummary,
    panelPhase,
    readState,
    writeState,
    safeLogDetail,
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
    scheduleRecordDates, readBookingSnapshot, decideBookingPreparation, applyBookingDecision, selectionReady,
    readBookingFormReadiness,
    prepareBookingFormForSubmit,
    resolveBookingSubmitSettleMs,
    waitForBookingSubmitSettle,
    findSubmitControl,
    triggerSubmitControl,
    markSubmitInProgress,
    resolveMemberSelection,
    inspectBookingPage,
  };

  if (DISABLE_AUTO_START) {
    return;
  }

  if (!submissionBlocked() && readSettings().runtime.autoStart && !readState().running) {
    patchState((state) => ({ ...state, running: true }));
  }
  bootstrapController();
})();
