---
name: ux-ui-auditor
description: Dedicated real-backend + real-browser UX, UI, Action Flow, i18n (EN/HE), modal-trap, and data-sync QA Auditor Agent for NutriAgent. Activate on every frontend, Alpine.js, CSS, popup/modal, auth, or translation change.
---

# NutriAgent UX/UI & Full-Flow E2E Auditor Agent

Use this skill whenever modifying `static/index.html`, `static/js/translations.js`, `static/sw.js`, or any frontend-facing endpoints in `bot_server.py`.

## Automated Real-Backend + Real-Browser E2E Audit

Run the auditor after edits (launches an isolated `uvicorn` backend with `NUTRI_E2E_OFFLINE=1` at **$0 cost** + headless Chrome/Edge CDP):

```powershell
node tools/ux_ui_e2e_auditor.mjs
```

### What It Verifies Automatically End-to-End
1. **Auth & Stale-Session Self-Healing:** Boots with an invalid cached token and verifies `/api/auth/me` issues a valid signed `v1.` session token.
2. **Real User Click & Save Flows:** Actually clicks Favorite preset chips (`POST /api/meals/`), logs an AI text meal (`POST /api/meals/ai`), logs water (`POST /api/water/add`), logs weight (`POST /api/weight`), updates/deletes a meal (`PUT`/`DELETE /api/meals/{id}`), updates daily targets (`PUT /api/targets`), submits support ticket (`POST /api/support/contact`), and opens the Admin dashboard — failing immediately on ANY HTTP `>= 400` response or red error toast.
3. **Top Strip <-> History Diary Sync:** Verifies Saturday (`885` kcal, `62g` protein, `1.5L` water) stays synced after `translations.js` `MutationObserver` debounce in both `EN` and `HE`.
4. **English Mode Leak Scan & Modal Traps:** Traverses `summary`, `history`, `recipes`, and opens/closes 11 modals (including Push Notifications) verifying **0 Hebrew leaks** and working `closeAllModals()`.
5. **Mobile (`390px`) & Desktop (`1280px`) Layout + Zero Console Errors:** Checks zero horizontal overflow and verifies mobile Push bell visibility.

## Paired Static & Prompt Checks
```powershell
python qa_guardian.py
python evals_runner.py
```
