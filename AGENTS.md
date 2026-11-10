# NutriAgent Workspace Rules & Mandatory UX/UI QA Gate

Whenever you modify any frontend, UI, styling, Alpine.js, Service Worker, or translation file (`static/index.html`, `static/js/translations.js`, `static/sw.js`) or any backend API endpoint consumed by the UI in `bot_server.py`:

1. **Mandatory Real-Browser UX/UI Audit Agent Verification:**
   - Read and follow the UX/UI Auditor skill at `.agent/skills/ux-ui-auditor/SKILL.md`.
   - Before concluding your work or pushing to git, you MUST run the real headless Chrome/Edge E2E auditor:
     ```powershell
     node tools/ux_ui_e2e_auditor.mjs
     ```
   - Alternatively, for large UI refactors, define and invoke a dedicated `ux-ui-qa-auditor` subagent (`define_subagent` / `invoke_subagent`) to run `node tools/ux_ui_e2e_auditor.mjs` plus inspect any new tabs/modals added in your change.

2. **Mandatory Bilingual & Synchronization Checks (EN/LTR + HE/RTL):**
   - Every new user-facing tab, card, or popup modal MUST have zero untranslated Hebrew leaks in English (`en`) mode (Admin-only sections are exempt unless requested).
   - Dynamic reactive counters (`x-text`, strip previews, History totals, macro counters) MUST remain synchronized across views (`weeklyStripDays` <-> `filteredHistoryMeals`) in BOTH English and Hebrew after `translations.js` `MutationObserver` runs.
   - Any change to `static/index.html`, `static/js/translations.js`, or `static/sw.js` requires keeping the `?v=...` cache-buster and `nutriagent-v...` Service Worker version synchronized.

3. **Backend & Push Gates:**
   - Run `python qa_guardian.py` (must be 36/36).
   - Run `python evals_runner.py` when touching `bot_server.py` AI/evals logic (must be 14/14).
   - Never bypass `.git/hooks/pre-push` (`qa_guardian.py` + `ux_ui_e2e_auditor.mjs`).
