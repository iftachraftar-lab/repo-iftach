---
name: ux-ui-auditor
description: Dedicated real-browser UX, UI, i18n (EN/HE), modal-trap, and data-sync QA Auditor Agent for NutriAgent. Activate on every frontend, Alpine.js, CSS, popup/modal, or translation change, or when investigating UI regressions.
---

# NutriAgent UX/UI QA Auditor Agent

Use this skill whenever modifying `static/index.html`, `static/js/translations.js`, `static/sw.js`, or frontend-facing endpoints in `bot_server.py`.

## Automated Real-Browser Checks

Run the Headless Chrome/Edge CDP E2E auditor after edits:

```powershell
node tools/ux_ui_e2e_auditor.mjs
```

### What the UX/UI Auditor Verifies in Real Headless Chrome/Edge
1. **Zero Runtime Errors:** Fails on any uncaught JS/Alpine exceptions or `console.error`.
2. **English Mode (`en` / `dir="ltr"`) Leak Scan:** Switches language to English, traverses `summary`, `history`, and `recipes` tabs, and opens interactive popups/modals (`openTargetModal`, `showBmiWizard`, `showHoldonAffiliateModal`, `showTrialWorkoutModal`, `showExpertsDirectoryModal`, `showWorkoutModal`, `showAchievementsModal`, `showGroceryModal`, `openBarcodeModal`, `showSupportModal`) verifying **0 Hebrew text leaks** after `translations.js` `MutationObserver` runs.
3. **Top Strip <-> History Diary Sync:** Verifies clicking/selecting a past day (e.g. Saturday with `885` kcal) updates `activeStripPreviewDay` accurately (`🔥 885`, `💪 62g`, `💧 1.5L`) without `translations.js` reverting dynamic text to `0`, and matches `selectedDateCalories` in the full History diary in both `en` and `he`.
4. **Modal Close & Escape Paths:** Verifies every tested modal closes cleanly via `closeAllModals()`.
5. **Responsive Overflow:** Checks zero horizontal overflow (`scrollWidth <= innerWidth + 2`) on Desktop (`1280px`) and Mobile (`390px`).

## Extending Coverage for New Modals or Flows
Whenever a new popup modal, tab, or dynamic counter widget is added to `static/index.html`, add its flag/flow to `modalsToTest` or the DOM assertions in `tools/ux_ui_e2e_auditor.mjs` so future pre-push runs guard it automatically.

## Paired Static & Prompt Checks
```powershell
python qa_guardian.py
python evals_runner.py
```
