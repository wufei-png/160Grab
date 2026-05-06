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
