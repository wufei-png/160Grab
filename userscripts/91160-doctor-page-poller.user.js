// ==UserScript==
// @name         160Grab 91160 Doctor Page Poller
// @namespace    https://github.com/wufei-png/160Grab
// @version      0.1.0
// @description  Poll a real 91160 doctor detail page, jump into ystep1, and auto-submit the booking form.
// @author       OpenAI Codex
// @match        https://www.91160.com/doctors/index/*
// @match        https://www.91160.com/guahao/ystep1/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function () {
  "use strict";

  const CONFIG = {
    target: { unitId: null, depId: null, doctorId: null },
    member: { memberId: "...", memberLabel: null },
    filters: { weeks: [], days: [], hours: [] },
    pacing: {
      pollMs: [1200, 2200],
      pageActionMs: [600, 1200],
      bookingRetryMs: [2000, 4000],
      rateLimitCooldownMs: [10000, 20000],
    },
    booking: { autoSubmit: true, maxAttemptsPerSlot: 3 },
  };

  const STORAGE_KEY = "grab160.doctorPagePoller.v1";
  const PANEL_POSITION_KEY = "grab160.doctorPagePoller.panelPosition.v1";
  const PANEL_ID = "grab160-doctor-page-poller-panel";
  const PANEL_DEFAULT_MARGIN = 16;
  const PANEL_VIEWPORT_MARGIN = 8;
  const RATE_LIMIT_PATTERNS = [
    "单位时间内访问次数过多",
    "访问次数过多",
    "访问过于频繁",
    "操作过于频繁",
  ];
  const PLACEHOLDER_VALUES = new Set(["", "...", "null", "undefined", "<member_id>"]);
  const DISABLE_AUTO_START = Boolean(
    globalThis.__GRAB160_DOCTOR_POLLER_DISABLE_AUTO_START__,
  );

  function compactText(value) {
    return String(value ?? "").replace(/\s+/g, " ").trim();
  }

  function normalizeOptionalValue(value) {
    const text = compactText(value).toLowerCase();
    return PLACEHOLDER_VALUES.has(text) ? null : compactText(value);
  }

  function normalizeDoctorTargetConfig(target) {
    const normalized = {
      unitId: normalizeOptionalValue(target?.unitId),
      depId: normalizeOptionalValue(target?.depId),
      doctorId: normalizeOptionalValue(target?.doctorId),
    };
    if (!normalized.unitId && !normalized.depId && !normalized.doctorId) {
      return null;
    }
    return normalized;
  }

  function normalizeMemberConfig(member) {
    return {
      memberId: normalizeOptionalValue(member?.memberId),
      memberLabel: normalizeOptionalValue(member?.memberLabel),
    };
  }

  function normalizeFilters(filters) {
    return {
      weeks: Array.isArray(filters?.weeks)
        ? filters.weeks
            .map((value) => Number.parseInt(String(value), 10))
            .filter((value) => Number.isInteger(value) && value >= 1 && value <= 7)
        : [],
      days: Array.isArray(filters?.days)
        ? filters.days
            .map((value) => compactText(value).toLowerCase())
            .filter(Boolean)
        : [],
      hours: Array.isArray(filters?.hours)
        ? filters.hours.map((value) => normalizeHourValue(value))
        : [],
    };
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

  function areTargetsCompatible(expected, actual) {
    if (!expected || !actual) {
      return true;
    }
    if (expected.unitId && actual.unitId && expected.unitId !== actual.unitId) {
      return false;
    }
    if (expected.depId && actual.depId && expected.depId !== actual.depId) {
      return false;
    }
    if (expected.doctorId && actual.doctorId && expected.doctorId !== actual.doctorId) {
      return false;
    }
    return true;
  }

  function mergeTargets(preferred, discovered) {
    const merged = {
      unitId: preferred?.unitId ?? discovered?.unitId ?? null,
      depId: preferred?.depId ?? discovered?.depId ?? null,
      doctorId: preferred?.doctorId ?? discovered?.doctorId ?? null,
    };
    return merged;
  }

  function isCompleteTarget(target) {
    return Boolean(target?.unitId && target?.depId && target?.doctorId);
  }

  function isResolvedTarget(target) {
    return isCompleteTarget(target) && compactText(target.depId) !== "0";
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
    const scheduleRowIds = Array.from(doc.querySelectorAll("li.liClassData[id]"))
      .map((element) => compactText(element.id))
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
      scheduleRowIds,
    };
  }

  function resolveTargetFromSnapshot(snapshot, configuredTarget) {
    const normalizedConfig = normalizeDoctorTargetConfig(configuredTarget);
    const candidates = [];
    const urlTarget = parseDoctorPageUrl(snapshot?.href ?? "");
    if (urlTarget) {
      candidates.push({ source: "url", target: urlTarget });
    }

    const attrTarget = targetFromAttrs(snapshot?.addMarkAttrs);
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

    const compatibleCandidates = candidates.filter((candidate) =>
      areTargetsCompatible(normalizedConfig, candidate.target),
    );
    const winner =
      compatibleCandidates.find((candidate) => isResolvedTarget(candidate.target)) ??
      compatibleCandidates.find((candidate) => isCompleteTarget(candidate.target));
    const mergedTarget = mergeTargets(
      normalizedConfig,
      winner?.target ?? compatibleCandidates[0]?.target,
    );

    if (!mergedTarget.doctorId) {
      return {
        ok: false,
        reason: "Could not resolve doctor_id from the current doctor page.",
      };
    }
    if (!mergedTarget.unitId || !mergedTarget.depId) {
      return {
        ok: false,
        reason: "Could not resolve full unit_id/dep_id from the current doctor page.",
      };
    }
    return {
      ok: true,
      target: {
        unitId: mergedTarget.unitId,
        depId: mergedTarget.depId,
        doctorId: mergedTarget.doctorId,
      },
      source: winner?.source ?? compatibleCandidates[0]?.source ?? "config",
    };
  }

  function parseSleepConfig(value) {
    if (Array.isArray(value) && value.length === 2) {
      const min = Number(value[0]);
      const max = Number(value[1]);
      if (Number.isFinite(min) && Number.isFinite(max)) {
        return min <= max ? [min, max] : [max, min];
      }
    }
    const single = Number(value);
    return Number.isFinite(single) ? [single, single] : [0, 0];
  }

  function pickDelayMs(value) {
    const [min, max] = parseSleepConfig(value);
    if (max <= min) {
      return Math.max(0, Math.round(min));
    }
    const random = Math.random() * (max - min);
    return Math.max(0, Math.round(min + random));
  }

  function sleepMs(delayMs) {
    return new Promise((resolve) => {
      setTimeout(resolve, Math.max(0, delayMs));
    });
  }

  function normalizePanelPosition(position) {
    const left = Number(position?.left);
    const top = Number(position?.top);
    if (!Number.isFinite(left) || !Number.isFinite(top)) {
      return null;
    }
    return { left: Math.round(left), top: Math.round(top) };
  }

  function clampPanelPosition(position, viewport, panelSize) {
    const normalized = normalizePanelPosition(position);
    const viewportWidth = Number(viewport?.width);
    const viewportHeight = Number(viewport?.height);
    const panelWidth = Number(panelSize?.width);
    const panelHeight = Number(panelSize?.height);
    if (
      !normalized ||
      !Number.isFinite(viewportWidth) ||
      !Number.isFinite(viewportHeight) ||
      !Number.isFinite(panelWidth) ||
      !Number.isFinite(panelHeight)
    ) {
      return normalized;
    }
    const maxLeft = Math.max(
      PANEL_VIEWPORT_MARGIN,
      viewportWidth - panelWidth - PANEL_VIEWPORT_MARGIN,
    );
    const maxTop = Math.max(
      PANEL_VIEWPORT_MARGIN,
      viewportHeight - panelHeight - PANEL_VIEWPORT_MARGIN,
    );
    return {
      left: Math.min(maxLeft, Math.max(PANEL_VIEWPORT_MARGIN, normalized.left)),
      top: Math.min(maxTop, Math.max(PANEL_VIEWPORT_MARGIN, normalized.top)),
    };
  }

  function getPanelPositionStorage() {
    return globalThis.localStorage ?? null;
  }

  function readPanelPosition() {
    const storage = getPanelPositionStorage();
    if (!storage?.getItem) {
      return null;
    }
    try {
      return normalizePanelPosition(JSON.parse(storage.getItem(PANEL_POSITION_KEY)));
    } catch (_error) {
      return null;
    }
  }

  function writePanelPosition(position) {
    const storage = getPanelPositionStorage();
    const normalized = normalizePanelPosition(position);
    if (!storage?.setItem || !normalized) {
      return;
    }
    storage.setItem(PANEL_POSITION_KEY, JSON.stringify(normalized));
  }

  function clearPanelPosition() {
    const storage = getPanelPositionStorage();
    storage?.removeItem?.(PANEL_POSITION_KEY);
  }

  function applyDefaultPanelPosition(panel) {
    panel.style.top = `${PANEL_DEFAULT_MARGIN}px`;
    panel.style.right = `${PANEL_DEFAULT_MARGIN}px`;
    panel.style.left = "auto";
    panel.style.bottom = "auto";
  }

  function getPanelRect(panel) {
    if (typeof panel.getBoundingClientRect === "function") {
      return panel.getBoundingClientRect();
    }
    return {
      left: Number.parseFloat(panel.style.left || "0") || 0,
      top: Number.parseFloat(panel.style.top || "0") || 0,
      width: Number.parseFloat(panel.style.width || "320") || 320,
      height: Number.parseFloat(panel.style.height || "120") || 120,
    };
  }

  function applyPanelPosition(panel, position) {
    const clamped = clampPanelPosition(
      position,
      { width: globalThis.innerWidth, height: globalThis.innerHeight },
      {
        width: panel.offsetWidth || getPanelRect(panel).width,
        height: panel.offsetHeight || getPanelRect(panel).height,
      },
    );
    if (!clamped) {
      applyDefaultPanelPosition(panel);
      return null;
    }
    panel.style.top = `${clamped.top}px`;
    panel.style.left = `${clamped.left}px`;
    panel.style.right = "auto";
    panel.style.bottom = "auto";
    return clamped;
  }

  function restorePanelPosition(panel) {
    const savedPosition = readPanelPosition();
    if (!savedPosition) {
      applyDefaultPanelPosition(panel);
      return null;
    }
    return applyPanelPosition(panel, savedPosition);
  }

  function installPanelDragging(panel, title) {
    if (!panel || !title || title.dataset.dragInstalled === "1") {
      return;
    }
    title.dataset.dragInstalled = "1";
    title.style.cursor = "move";
    title.style.userSelect = "none";
    title.title = "Drag to move. Double-click to reset position.";

    let dragState = null;
    const finishDrag = () => {
      if (!dragState) {
        return;
      }
      const finalPosition = applyPanelPosition(panel, {
        left: getPanelRect(panel).left,
        top: getPanelRect(panel).top,
      });
      if (finalPosition) {
        writePanelPosition(finalPosition);
      }
      dragState = null;
    };

    title.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) {
        return;
      }
      const rect = getPanelRect(panel);
      dragState = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        left: rect.left,
        top: rect.top,
      };
      panel.style.left = `${rect.left}px`;
      panel.style.top = `${rect.top}px`;
      panel.style.right = "auto";
      panel.style.bottom = "auto";
      title.setPointerCapture?.(event.pointerId);
      event.preventDefault();
    });

    title.addEventListener("pointermove", (event) => {
      if (!dragState || event.pointerId !== dragState.pointerId) {
        return;
      }
      applyPanelPosition(panel, {
        left: dragState.left + (event.clientX - dragState.startX),
        top: dragState.top + (event.clientY - dragState.startY),
      });
      event.preventDefault();
    });

    title.addEventListener("pointerup", (event) => {
      if (!dragState || event.pointerId !== dragState.pointerId) {
        return;
      }
      finishDrag();
    });
    title.addEventListener("pointercancel", finishDrag);
    title.addEventListener("lostpointercapture", finishDrag);
    title.addEventListener("dblclick", () => {
      clearPanelPosition();
      applyDefaultPanelPosition(panel);
    });

    globalThis.addEventListener?.("resize", () => {
      const currentPosition =
        normalizePanelPosition({
          left: getPanelRect(panel).left,
          top: getPanelRect(panel).top,
        }) ?? readPanelPosition();
      const applied = applyPanelPosition(panel, currentPosition);
      if (applied) {
        writePanelPosition(applied);
      }
    });
  }

  function createPanel() {
    let panel = document.getElementById(PANEL_ID);
    if (panel) {
      return panel;
    }
    panel = document.createElement("div");
    panel.id = PANEL_ID;
    panel.innerHTML = `
      <div class="grab160-title">160Grab Doctor Poller</div>
      <div class="grab160-status">Starting...</div>
      <div class="grab160-detail"></div>
    `;
    Object.assign(panel.style, {
      position: "fixed",
      zIndex: "999999",
      width: "320px",
      padding: "12px 14px",
      borderRadius: "10px",
      boxShadow: "0 8px 28px rgba(0, 0, 0, 0.18)",
      background: "rgba(17, 24, 39, 0.92)",
      color: "#f3f4f6",
      fontSize: "13px",
      lineHeight: "1.5",
      fontFamily:
        "\"SFMono-Regular\", \"Menlo\", \"Monaco\", \"Cascadia Mono\", monospace",
    });
    const title = panel.querySelector(".grab160-title");
    const detail = panel.querySelector(".grab160-detail");
    if (title) {
      title.style.fontWeight = "700";
      title.style.marginBottom = "6px";
      title.style.paddingRight = "20px";
    }
    if (detail) {
      detail.style.opacity = "0.88";
      detail.style.marginTop = "6px";
      detail.style.whiteSpace = "pre-wrap";
    }
    document.documentElement.appendChild(panel);
    restorePanelPosition(panel);
    installPanelDragging(panel, title);
    return panel;
  }

  function setStatus(level, message, detail) {
    const panel = createPanel();
    const statusNode = panel.querySelector(".grab160-status");
    const detailNode = panel.querySelector(".grab160-detail");
    const colorMap = {
      info: "#93c5fd",
      success: "#86efac",
      warn: "#fcd34d",
      error: "#fca5a5",
    };
    if (statusNode) {
      statusNode.textContent = `[${level.toUpperCase()}] ${message}`;
      statusNode.style.color = colorMap[level] ?? "#f3f4f6";
    }
    if (detailNode) {
      const raw = String(detail ?? "");
      detailNode.textContent = raw.includes("\n") ? raw.trimEnd() : compactText(detail);
    }
    const logger = level === "error" ? console.error : level === "warn" ? console.warn : console.log;
    logger(`[160Grab Poller] ${message}`, detail ?? "");
  }

  function defaultState() {
    return {
      version: 1,
      lastTarget: null,
      pendingBooking: null,
      slotAttempts: {},
    };
  }

  function readState() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (!raw) {
        return defaultState();
      }
      const parsed = JSON.parse(raw);
      return {
        ...defaultState(),
        ...parsed,
        slotAttempts: { ...defaultState().slotAttempts, ...(parsed.slotAttempts ?? {}) },
      };
    } catch (_error) {
      return defaultState();
    }
  }

  function writeState(state) {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }

  function patchState(mutator) {
    const current = readState();
    const next = mutator({ ...current, slotAttempts: { ...current.slotAttempts } });
    writeState(next);
    return next;
  }

  function rememberTarget(target) {
    patchState((state) => ({ ...state, lastTarget: target }));
  }

  function schedulePendingBooking(target, scheduleId, attempt = 0) {
    patchState((state) => ({
      ...state,
      lastTarget: target,
      pendingBooking: {
        unitId: target.unitId,
        depId: target.depId,
        doctorId: target.doctorId,
        scheduleId,
        attempt,
      },
    }));
  }

  function clearPendingBooking() {
    patchState((state) => ({ ...state, pendingBooking: null }));
  }

  function recordSlotAttempts(scheduleId, attempts) {
    patchState((state) => ({
      ...state,
      slotAttempts: {
        ...state.slotAttempts,
        [scheduleId]: Math.max(Number(state.slotAttempts[scheduleId] ?? 0), attempts),
      },
    }));
  }

  function getSlotAttempts(scheduleId) {
    return Number(readState().slotAttempts[scheduleId] ?? 0);
  }

  function iterTexts(payload, output) {
    if (typeof payload === "string") {
      output.push(payload);
      return output;
    }
    if (Array.isArray(payload)) {
      for (const item of payload) {
        iterTexts(item, output);
      }
      return output;
    }
    if (payload && typeof payload === "object") {
      for (const value of Object.values(payload)) {
        iterTexts(value, output);
      }
    }
    return output;
  }

  function extractRateLimitMessage(payload) {
    const texts = iterTexts(payload, []);
    for (const text of texts) {
      const compact = compactText(text);
      for (const pattern of RATE_LIMIT_PATTERNS) {
        if (compact.includes(pattern)) {
          return compact;
        }
      }
    }
    return null;
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
      if (hour < 0 || hour > 23 || minute < 0 || minute > 59) {
        throw new Error("Invalid hour format");
      }
      return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
    }
    throw new Error("Invalid hour format");
  }

  function normalizeHourValue(value) {
    const text = compactText(value);
    const parts = text.split("-");
    if (parts.length !== 2) {
      throw new Error(
        "Invalid hour format. Use HH:MM-HH:MM, H-H, H.5-H, or mixed variants like 9:30-10.",
      );
    }
    return `${normalizeHourEndpoint(parts[0])}-${normalizeHourEndpoint(parts[1])}`;
  }

  function parseTimeRange(value) {
    const text = compactText(value);
    if (!text.includes("-")) {
      return null;
    }
    const [startText, endText] = text.split("-", 2);
    const start = parseTimeToMinutes(startText);
    const end = parseTimeToMinutes(endText);
    if (start === null || end === null || start >= end) {
      return null;
    }
    return [start, end];
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

  function rangesOverlap(slotStart, slotEnd, filterRange) {
    return slotStart < filterRange[1] && slotEnd > filterRange[0];
  }

  function slotMatchesHours(slot, hours) {
    if (!hours.length) {
      return true;
    }
    if (!compactText(slot.timeRange)) {
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
    if (value === 1) {
      return "available";
    }
    if (value === 0) {
      return "full";
    }
    if (value === -1) {
      return "expired";
    }
    if (value === -2) {
      return "stopped";
    }
    if (value === -3) {
      return "not_open";
    }
    return "unavailable";
  }

  function walkScheduleTree(node, path = [], output = []) {
    if (!node || typeof node !== "object") {
      return output;
    }
    if (Object.prototype.hasOwnProperty.call(node, "schedule_id") &&
        Object.prototype.hasOwnProperty.call(node, "y_state")) {
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
    const weekdayMap = { "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7 };
    const labels = payload.dates ?? {};
    return walkScheduleTree(payload.sch).map(([path, item]) => {
      const dateKey = [...path].reverse().find((part) => /^\d{4}-\d{2}-\d{2}$/.test(part)) || compactText(item.to_date);
      const halfKey =
        [...path].reverse().find((part) => /_(am|pm|em)$/i.test(part)) || "";
      const dayPeriod =
        compactText(item.day_period).toLowerCase() ||
        compactText(halfKey.split("_").pop()).toLowerCase();
      return {
        scheduleId: compactText(item.schedule_id),
        doctorId: compactText(item.doctor_id) || target.doctorId,
        weekday: weekdayMap[compactText(labels[dateKey])] ?? 0,
        dayPeriod,
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
      if (filters.days.length && !filters.days.includes(compactText(slot.dayPeriod).toLowerCase())) {
        return false;
      }
      if (!slotMatchesHours(slot, filters.hours)) {
        return false;
      }
      return true;
    });
  }

  function isBookableSlot(slot) {
    const status = compactText(slot.status).toLowerCase();
    if (!status || status === "available") {
      return true;
    }
    return ["can_booking", "open", "normal"].includes(status);
  }

  function pickNextSlot(slots, slotAttempts, maxAttemptsPerSlot) {
    const availableSlots = slots.filter(isBookableSlot);
    return (
      availableSlots.find(
        (slot) => Number(slotAttempts[slot.scheduleId] ?? 0) < maxAttemptsPerSlot,
      ) ?? null
    );
  }

  function findCurrentUserKey() {
    const userKey = compactText(globalThis._user_key);
    return userKey || null;
  }

  async function fetchJsonInsidePage(url, params) {
    if (globalThis.jQuery?.ajax) {
      return await new Promise((resolve, reject) => {
        globalThis.jQuery.ajax({
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
    }
    const requestUrl = new URL(url, location.origin);
    for (const [key, value] of Object.entries(params ?? {})) {
      requestUrl.searchParams.set(key, value);
    }
    const response = await fetch(requestUrl.toString(), { credentials: "include" });
    const text = await response.text();
    try {
      return JSON.parse(text);
    } catch (error) {
      throw new Error(
        `fetch returned non-JSON status=${response.status} body=${compactText(text).slice(0, 200)}`,
      );
    }
  }

  async function fetchDoctorSchedule(target) {
    const userKey = findCurrentUserKey();
    if (!userKey) {
      return { result_code: 0, error_code: "10021", error_msg: "请登录后查看医生号源" };
    }
    return await fetchJsonInsidePage(
      "https://gate.91160.com/guahao/v1/pc/sch/doctor",
      {
        user_key: userKey,
        docid: target.doctorId,
        doc_id: target.doctorId,
        unit_id: target.unitId,
        dep_id: target.depId,
        date: new Date().toISOString().slice(0, 10),
        days: "6",
      },
    );
  }

  function createVisibleMessagesSnapshot() {
    return Array.from(
      document.querySelectorAll(
        ".wrong,.warning,.import,.fine,.tips,.msg,.message,.error,.err,.layui-layer-content,.select-member-close,.select-vertifycode-close,.tip,.order-tit",
      ),
    )
      .map((element) => compactText(element.textContent))
      .filter(Boolean);
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

  function memberRadioLabelText(radio) {
    const container = radio.closest("tr, li, label, .patient_item, .member_item, .person_item");
    if (container) {
      return compactText(container.textContent);
    }
    return compactText(radio.parentElement?.textContent);
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
    const memberRow = radio.closest(
      'tr[id^="mem"], [data-member-id], [data-mid], .member_item, .patient_item, .person_item',
    );
    if (memberRow) {
      return true;
    }
    return false;
  }

  function collectRadioGroups() {
    const radios = Array.from(document.querySelectorAll('input[type="radio"]'));
    const memberRadios = radios.filter((radio) => isLikelyMemberRadio(radio));
    const ignoredRadios = radios.filter((radio) => !isLikelyMemberRadio(radio));
    return { radios, memberRadios, ignoredRadios };
  }

  function findMemberRadios() {
    return collectRadioGroups().memberRadios;
  }

  function memberRadioDebugRows(radios) {
    return radios.map((radio, index) => {
      const form = radio.form;
      const formHint = form
        ? compactText([form.id, form.getAttribute("name"), form.className].filter(Boolean).join(" "))
        : "";
      return {
        index,
        value: compactText(radio.value),
        name: radio.getAttribute("name") ?? "",
        id: radio.getAttribute("id") ?? "",
        checked: Boolean(radio.checked),
        disabled: Boolean(radio.disabled),
        form: formHint,
        label: memberRadioLabelText(radio),
      };
    });
  }

  function memberRadioDebugSummary(memberRadios, ignoredRadios = []) {
    const totalCount = memberRadios.length + ignoredRadios.length;
    if (!totalCount) {
      return "No <input type=\"radio\"> elements were found on the page.";
    }
    const memberRows = memberRadioDebugRows(memberRadios);
    const ignoredRows = memberRadioDebugRows(ignoredRadios);
    const lines = [
      `Found ${memberRadios.length} candidate member radio(s) out of ${totalCount} total <input type="radio"> element(s).`,
      ...(memberRows.length
        ? memberRows.map(
            (row) =>
              `member#${row.index}\tvalue=${JSON.stringify(row.value)}\tname=${JSON.stringify(row.name)}\tid=${JSON.stringify(row.id)}\tchecked=${row.checked}\tdisabled=${row.disabled}\tform=${JSON.stringify(row.form)}\tlabel=${JSON.stringify(row.label)}`,
          )
        : ["No candidate member radio matched the current filters."]),
    ];
    if (ignoredRows.length) {
      lines.push(
        "",
        `Ignored ${ignoredRadios.length} non-member radio(s). These do not count as member choices:`,
        ...ignoredRows.map(
        (row) =>
            `ignored#${row.index}\tvalue=${JSON.stringify(row.value)}\tname=${JSON.stringify(row.name)}\tid=${JSON.stringify(row.id)}\tchecked=${row.checked}\tdisabled=${row.disabled}\tform=${JSON.stringify(row.form)}\tlabel=${JSON.stringify(row.label)}`,
        ),
      );
    }
    lines.push(
      "",
      "Set CONFIG.member.memberId to a matching \"value\", or CONFIG.member.memberLabel to a substring of a \"label\".",
    );
    return lines.join("\n");
  }

  function resolveMemberSelection(memberConfig) {
    const { memberRadios, ignoredRadios } = collectRadioGroups();
    const debugRadios = () => memberRadioDebugRows(memberRadios);
    const hiddenMemberId =
      compactText(document.querySelector('input[name="member_id"]')?.value) ||
      compactText(document.querySelector('input[name="mid"]')?.value) ||
      compactText(document.querySelector('#member_id')?.value);
    if (memberConfig.memberId) {
      const radio = memberRadios.find(
        (candidate) =>
          compactText(candidate.value) === memberConfig.memberId ||
          compactText(candidate.getAttribute("data-member-id")) === memberConfig.memberId ||
          compactText(candidate.getAttribute("data-mid")) === memberConfig.memberId,
      );
      if (!radio && hiddenMemberId && hiddenMemberId === memberConfig.memberId) {
        return { ok: true, memberId: memberConfig.memberId, radio: null };
      }
      if (!radio && (memberRadios.length > 0 || ignoredRadios.length > 0)) {
        return {
          ok: false,
          reason: [
            `Configured member_id ${memberConfig.memberId} was not found on the booking page.`,
            memberRadioDebugSummary(memberRadios, ignoredRadios),
          ].join("\n\n"),
          debugRadios: debugRadios(),
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
            `Configured memberLabel ${memberConfig.memberLabel} was not found on the booking page.`,
            memberRadioDebugSummary(memberRadios, ignoredRadios),
          ].join("\n\n"),
          debugRadios: debugRadios(),
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
          "Booking page exposes multiple candidate member choices. Fill CONFIG.member.memberId or CONFIG.member.memberLabel first.",
          memberRadioDebugSummary(memberRadios, ignoredRadios),
        ].join("\n\n"),
        debugRadios: debugRadios(),
      };
    }
    if (hiddenMemberId) {
      return { ok: true, memberId: hiddenMemberId, radio: null };
    }
    return {
      ok: false,
      reason: "Booking page does not expose a selectable member and no member_id is configured.",
    };
  }

  function fillBookingForm(formState, memberSelection) {
    if (formState.appointmentValue) {
      const appointmentElement = formState.appointmentOptions.find(
        (option) => option.value === formState.appointmentValue,
      )?.element;
      if (appointmentElement) {
        clickElement(appointmentElement);
      }
    }

    const hiddenSelectors = [
      'input[name="member_id"]',
      "#member_id",
      'input[name="memberId"]',
      "#memberId",
      'input[name="mid"]',
      "#mid",
      'input[name="his_mem_id"]',
      "#his_mem_id",
    ];
    for (const selector of hiddenSelectors) {
      const input = document.querySelector(selector);
      if (input) {
        input.value = memberSelection.memberId;
      }
    }

    if (memberSelection.radio) {
      clickElement(memberSelection.radio);
    }

    const diseaseSelectors = [
      'input[name="disease_input"]',
      "#disease_input",
      'textarea[name="disease_content"]',
      "#disease_content",
    ];
    for (const selector of diseaseSelectors) {
      const input = document.querySelector(selector);
      if (input && !compactText(input.value)) {
        input.value = "11111111111111";
      }
    }

    const acceptSelectors = ['input[name="accept"][value="1"]', "#check_yuyue_rule"];
    for (const selector of acceptSelectors) {
      const input = document.querySelector(selector);
      if (input) {
        input.checked = true;
        input.setAttribute("checked", "checked");
      }
    }
  }

  function isVisible(element) {
    if (!element) {
      return false;
    }
    const style = globalThis.getComputedStyle(element);
    return style.display !== "none" && style.visibility !== "hidden";
  }

  function triggerSubmitControl() {
    const candidateSelectors = [
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
    ];
    for (const selector of candidateSelectors) {
      const element = document.querySelector(selector);
      if (isVisible(element)) {
        clickElement(element);
        return { method: "selector", target: selector };
      }
    }
    const textCandidates = Array.from(
      document.querySelectorAll('button, input[type="button"], input[type="submit"], a'),
    ).filter((element) => {
      const text = compactText(element.textContent || element.value);
      return isVisible(element) && /确认预约|提交预约|提交|预约|下一步/.test(text);
    });
    if (textCandidates.length > 0) {
      clickElement(textCandidates[0]);
      return {
        method: "text-match",
        target: compactText(textCandidates[0].textContent || textCandidates[0].value),
      };
    }
    const form = document.querySelector("form");
    if (form) {
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit();
        return { method: "requestSubmit", target: "form" };
      }
      form.submit();
      return { method: "submit", target: "form" };
    }
    return { method: "not-found", target: null };
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

  function inspectBookingPage(beforeUrl) {
    const currentUrl = location.href;
    const hasBookingForm = Boolean(document.querySelector("#suborder"));
    const hasSubmitButton = Boolean(
      document.querySelector(
        "#suborder #submitbtn, #suborder input[type='submit'], #suborder button[type='submit']",
      ),
    );
    const visibleMessages = createVisibleMessagesSnapshot();
    const rateLimitMessage = extractRateLimitMessage(visibleMessages);
    const leftBookingPage =
      currentUrl !== beforeUrl && !currentUrl.includes("/guahao/ystep1/");
    const success = leftBookingPage || (!hasBookingForm && !hasSubmitButton);
    return {
      success,
      currentUrl,
      hasBookingForm,
      hasSubmitButton,
      visibleMessages,
      rateLimitMessage,
    };
  }

  function bookingFailureMessage(formState) {
    if (formState.invalidReason === "no_appointment_options") {
      return `Booking form for schedule ${formState.scheduleId} exposes no appointment time options.`;
    }
    if (formState.invalidReason === "hour_filter_mismatch") {
      return `Booking form for schedule ${formState.scheduleId} has appointment times, but none match CONFIG.filters.hours.`;
    }
    if (formState.invalidReason === "missing_schedule_id") {
      return "Booking form does not expose schedule_id.";
    }
    return `Booking form is invalid for schedule ${formState.scheduleId || "<missing>"}.`;
  }

  function navigateToDoctorPage(target) {
    if (!isCompleteTarget(target)) {
      setStatus(
        "error",
        "Cannot return to the doctor page automatically.",
        "doctorId/unitId/depId is incomplete.",
      );
      return;
    }
    location.replace(buildDoctorUrl(target));
  }

  async function handleBookingFailure(target, formState, attempt, maxAttempts, reason, delayConfig) {
    const nextAttempt = attempt + 1;
    if (formState?.scheduleId) {
      recordSlotAttempts(formState.scheduleId, nextAttempt);
    }
    if (nextAttempt >= maxAttempts || formState?.invalidReason === "hour_filter_mismatch" || formState?.invalidReason === "no_appointment_options") {
      clearPendingBooking();
      setStatus(
        "warn",
        `Booking failed for schedule ${formState?.scheduleId || "<missing>"}. Returning to the doctor page.`,
        reason,
      );
      await sleepMs(pickDelayMs(CONFIG.pacing.bookingRetryMs));
      navigateToDoctorPage(target);
      return;
    }
    schedulePendingBooking(target, formState.scheduleId, nextAttempt);
    const delayMs = pickDelayMs(delayConfig);
    setStatus(
      "warn",
      `Booking attempt ${nextAttempt} failed. Retrying in ${delayMs} ms.`,
      reason,
    );
    await sleepMs(delayMs);
    location.replace(buildBookingUrl(target, formState.scheduleId));
  }

  async function runDoctorPageController() {
    const configuredTarget = normalizeDoctorTargetConfig(CONFIG.target);
    const filters = normalizeFilters(CONFIG.filters);
    const snapshot = snapshotCurrentDoctorPage();
    const resolved = resolveTargetFromSnapshot(snapshot, configuredTarget);
    if (!resolved.ok) {
      setStatus("error", "Userscript could not resolve the current doctor page target.", resolved.reason);
      return;
    }
    const target = resolved.target;
    if (configuredTarget && !areTargetsCompatible(configuredTarget, target)) {
      setStatus(
        "error",
        "Current doctor page does not match CONFIG.target.",
        JSON.stringify({ configuredTarget, target }),
      );
      return;
    }
    rememberTarget(target);

    const canonicalUrl = buildDoctorUrl(target);
    if (canonicalUrl !== `${location.origin}${location.pathname}`) {
      setStatus(
        "info",
        "Redirecting to the canonical doctor page before polling.",
        canonicalUrl,
      );
      location.replace(canonicalUrl);
      return;
    }

    if (!findCurrentUserKey()) {
      setStatus(
        "warn",
        "Current page cannot read _user_key. Re-login and refresh this doctor page first.",
        buildDoctorUrl(target),
      );
      return;
    }

    let attempt = 0;
    while (true) {
      attempt += 1;
      if (!findCurrentUserKey()) {
        setStatus(
          "warn",
          "Current page lost _user_key during polling. Re-login and refresh this doctor page first.",
          "",
        );
        return;
      }

      let payload;
      try {
        payload = await fetchDoctorSchedule(target);
      } catch (error) {
        setStatus("warn", `Doctor page polling request failed on attempt ${attempt}.`, error.message);
        await sleepMs(pickDelayMs(CONFIG.pacing.pollMs));
        continue;
      }

      if (String(payload?.error_code ?? "") === "10021" || compactText(payload?.error_msg).includes("请登录后查看医生号源")) {
        setStatus(
          "warn",
          "Current session is not allowed to query schedules. Re-login and refresh this doctor page first.",
          compactText(payload?.error_msg),
        );
        return;
      }

      const rateLimitMessage = extractRateLimitMessage(payload);
      if (rateLimitMessage) {
        const cooldownMs = pickDelayMs(CONFIG.pacing.rateLimitCooldownMs);
        setStatus(
          "warn",
          `Doctor page polling hit rate limiting. Cooling down for ${cooldownMs} ms.`,
          rateLimitMessage,
        );
        await sleepMs(cooldownMs);
        continue;
      }

      const slots = filterSlots(parseDoctorSchedulePayload(payload, target), target, filters);
      const nextSlot = pickNextSlot(slots, readState().slotAttempts, CONFIG.booking.maxAttemptsPerSlot);
      if (nextSlot) {
        schedulePendingBooking(target, nextSlot.scheduleId, 0);
        setStatus(
          "success",
          `Matched slot ${nextSlot.scheduleId}. Jumping to booking page.`,
          compactText(
            `${nextSlot.date} ${nextSlot.dayPeriod} ${nextSlot.timeRange}`.trim(),
          ),
        );
        await sleepMs(pickDelayMs(CONFIG.pacing.pageActionMs));
        location.replace(buildBookingUrl(target, nextSlot.scheduleId));
        return;
      }

      setStatus(
        "info",
        `Doctor page polling attempt ${attempt} found ${slots.length} matching slot(s), but none are ready to book yet.`,
        JSON.stringify({
          target,
          weeks: filters.weeks,
          days: filters.days,
          hours: filters.hours,
        }),
      );
      await sleepMs(pickDelayMs(CONFIG.pacing.pollMs));
    }
  }

  async function runBookingPageController() {
    const bookingTarget = parseBookingUrl(location.href);
    if (!bookingTarget) {
      setStatus("error", "Current booking page URL is unsupported.", location.href);
      return;
    }
    const state = readState();
    const memberConfig = normalizeMemberConfig(CONFIG.member);
    const filters = normalizeFilters(CONFIG.filters);
    const doctorTarget = {
      unitId: bookingTarget.unitId,
      depId: bookingTarget.depId,
      doctorId:
        normalizeOptionalValue(state.pendingBooking?.doctorId) ??
        normalizeOptionalValue(state.lastTarget?.doctorId) ??
        normalizeOptionalValue(CONFIG.target?.doctorId),
    };

    const existingPending = state.pendingBooking;
    const attempt = Number(existingPending?.attempt ?? 0);
    if (
      !existingPending ||
      existingPending.scheduleId !== bookingTarget.scheduleId ||
      existingPending.unitId !== bookingTarget.unitId ||
      existingPending.depId !== bookingTarget.depId
    ) {
      schedulePendingBooking(doctorTarget, bookingTarget.scheduleId, 0);
    }

    const rateLimitOnLoad = extractRateLimitMessage([
      document.body?.innerText ?? "",
      document.documentElement?.outerHTML ?? "",
    ]);
    if (rateLimitOnLoad) {
      await handleBookingFailure(
        doctorTarget,
        { scheduleId: bookingTarget.scheduleId, invalidReason: null },
        attempt,
        CONFIG.booking.maxAttemptsPerSlot,
        rateLimitOnLoad,
        CONFIG.pacing.rateLimitCooldownMs,
      );
      return;
    }

    const memberSelection = resolveMemberSelection(memberConfig);
    if (!memberSelection.ok) {
      if (memberSelection.debugRadios?.length) {
        console.warn("[160Grab Poller] Booking page radios (member resolution)", memberSelection.debugRadios);
        console.table(memberSelection.debugRadios);
      }
      setStatus("warn", "Booking page is waiting for a clearer member selection.", memberSelection.reason);
      return;
    }

    const formState = parseBookingFormState(filters, bookingTarget.scheduleId);
    if (!formState.isValid) {
      await handleBookingFailure(
        doctorTarget,
        formState,
        attempt,
        CONFIG.booking.maxAttemptsPerSlot,
        bookingFailureMessage(formState),
        CONFIG.pacing.bookingRetryMs,
      );
      return;
    }

    if (!CONFIG.booking.autoSubmit) {
      fillBookingForm(formState, memberSelection);
      setStatus(
        "info",
        "Booking form is prepared. autoSubmit is disabled, waiting for manual confirmation.",
        JSON.stringify({
          scheduleId: formState.scheduleId,
          appointmentLabel: formState.appointmentLabel,
          memberId: memberSelection.memberId,
        }),
      );
      return;
    }

    await sleepMs(pickDelayMs(CONFIG.pacing.pageActionMs));
    fillBookingForm(formState, memberSelection);

    const beforeUrl = location.href;
    await sleepMs(pickDelayMs(CONFIG.pacing.pageActionMs));
    const submitResult = triggerSubmitControl();
    if (submitResult.method === "not-found") {
      await handleBookingFailure(
        doctorTarget,
        formState,
        attempt,
        CONFIG.booking.maxAttemptsPerSlot,
        "Could not find a submit control on the booking page.",
        CONFIG.pacing.bookingRetryMs,
      );
      return;
    }

    const followupAction = await clickFollowupControl();
    if (followupAction) {
      setStatus("info", `Booking page triggered follow-up action ${followupAction}.`, "");
    }

    const checkpoints = [400, 900, 1600, 2400];
    let inspection = inspectBookingPage(beforeUrl);
    for (const checkpoint of checkpoints) {
      if (inspection.success || inspection.rateLimitMessage) {
        break;
      }
      await sleepMs(checkpoint);
      inspection = inspectBookingPage(beforeUrl);
    }

    if (inspection.success) {
      clearPendingBooking();
      recordSlotAttempts(formState.scheduleId, CONFIG.booking.maxAttemptsPerSlot);
      setStatus(
        "success",
        `Booking succeeded for schedule ${formState.scheduleId}.`,
        compactText(formState.appointmentLabel || inspection.currentUrl),
      );
      return;
    }

    await handleBookingFailure(
      doctorTarget,
      formState,
      attempt,
      CONFIG.booking.maxAttemptsPerSlot,
      inspection.rateLimitMessage ||
        inspection.visibleMessages.join(" | ") ||
        `Booking page stayed on ${inspection.currentUrl}`,
      inspection.rateLimitMessage
        ? CONFIG.pacing.rateLimitCooldownMs
        : CONFIG.pacing.bookingRetryMs,
    );
  }

  async function bootstrap() {
    const doctorTarget = parseDoctorPageUrl(location.href);
    if (doctorTarget) {
      await runDoctorPageController();
      return;
    }

    const bookingTarget = parseBookingUrl(location.href);
    if (bookingTarget) {
      await runBookingPageController();
      return;
    }

    setStatus(
      "warn",
      "Userscript only supports 91160 doctor detail pages and their derived ystep1 booking pages.",
      location.href,
    );
  }

  globalThis.__GRAB160_DOCTOR_POLLER_TEST_HOOKS__ = {
    CONFIG,
    normalizeOptionalValue,
    normalizeDoctorTargetConfig,
    normalizeFilters,
    parseDoctorPageUrl,
    parseBookingUrl,
    buildDoctorUrl,
    buildBookingUrl,
    resolveTargetFromSnapshot,
    normalizeHourValue,
    parseDoctorSchedulePayload,
    filterSlots,
    extractRateLimitMessage,
    chooseAppointmentOption,
    pickNextSlot,
    normalizePanelPosition,
    clampPanelPosition,
    findMemberRadios,
    resolveMemberSelection,
    memberRadioDebugSummary,
  };

  if (DISABLE_AUTO_START) {
    return;
  }

  void bootstrap().catch((error) => {
    setStatus("error", "Userscript crashed.", error?.stack || error?.message || String(error));
  });
})();
