"use strict";

// Test-only DOM snapshot. Actual DOM behavior is tested separately in Chromium.
const fs = require("node:fs");
const vm = require("node:vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
let clickCount = 0;
const options = input.options.map((option) => ({
  getAttribute: (name) => name === "val" ? option.value : null,
  textContent: option.label,
  click: () => { clickCount += 1; },
}));
const sandbox = {
  console,
  URL,
  setTimeout,
  clearTimeout,
  location: new URL("https://synthetic.invalid/guahao/ystep1/synthetic.html"),
  document: {
    querySelector(selector) {
      if (selector === 'input[name="schedule_id"]') {
        return input.schedule_id ? { value: input.schedule_id } : null;
      }
      if (selector === "#delts") {
        return { querySelectorAll: () => options };
      }
      throw new Error(`Unexpected parser selector: ${selector}`);
    },
    querySelectorAll: () => options,
  },
  __GRAB160_DOCTOR_POLLER_DISABLE_AUTO_START__: true,
};
sandbox.window = sandbox;
sandbox.unsafeWindow = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);
const parsed = sandbox.__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.parseBookingFormState(
  { hours: input.hours }, "",
);
process.stdout.write(JSON.stringify({
  parser: {
    schedule_id: parsed.scheduleId,
    appointment_value: parsed.appointmentValue,
    appointment_label: parsed.appointmentLabel,
    is_valid: parsed.isValid,
    invalid_reason: parsed.invalidReason,
  },
  click_count: clickCount,
}));
