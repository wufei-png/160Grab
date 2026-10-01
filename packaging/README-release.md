# 160Grab Release Bundle

This bundle ships a frozen `160Grab` binary together with a starter `config.yaml`.

## First Run

1. Open `config.yaml` and adjust the values for your account and workflow.
2. Start the bundled launcher:
   - Windows: double-click `160Grab.exe`
   - macOS: double-click `160Grab.command`
3. The program will open a Chromium window for the manual-login flow.

## Notes

- Python is not required on the target machine.
- The bundle includes only the Chromium browser runtime required by Playwright.
- These release artifacts are unsigned in v1. macOS Gatekeeper and Windows SmartScreen may show trust prompts.

- Login and CAPTCHA remain manual. Automatic final submission requires explicit local consent; `booking.submit_mode: manual_confirm` prepares the form for manual handoff. Only explicit configured or existing real values may be used.
- A final click without verified matching business evidence is `OUTCOME_UNKNOWN`. Stop and check the original site's booking records before resolving it. Restarting, changing profiles, revoking consent or deleting logs does not resolve pending attempts. Never delete the transaction journal to retry.
- Run `160Grab --pending-attempts` (Windows: `160Grab.exe`) to list opaque attempt IDs. After checking the original site, use `--resolve-attempt ATTEMPT_ID --resolution booked|not-booked` and type `VERIFIED` interactively. `--revoke-consent` does not clear pending attempts.
- Exit codes: 0 confirmed success, 1 confirmed no effect/failure, 2 manual action, 3 unknown outcome. No live result adapter is currently verified.
- Do not run Python and the userscript, different browser profiles, or multiple machines against the same target.
- `browser.channel` defaults to bundled Chromium; Chrome/Edge require separate installation and a matching independent profile. Naive start times use `schedule.timezone` (default Asia/Shanghai); prefer offset ISO timestamps.
- Ordinary logs/notifications contain only safe projected events. Raw HTML/screenshots require both `GRAB_DEBUG_DIR` and `logging.include_sensitive_debug: true`, remain local, and must be deleted within 24 hours. Do not upload raw captures, private configuration or profiles.
