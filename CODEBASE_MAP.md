# NutriAgent AI — Codebase & Architecture Navigation Map

> **Important for AI Agents & Developers:**  
> Use this navigation map to directly target specific line ranges and files instead of reading whole monolithic files.  
> This saves tens of thousands of tokens per task, guarantees ultra-fast lookups, and avoids regressions on the live system.

---

## 1. System Overview & Tech Stack

- **Backend:** Python 3.11+, FastAPI (`bot_server.py`, ~8,480 lines), SQLite (`data/nutrition.db`), Gemini AI SDK.
- **Frontend:** Single Page Application (PWA) in `static/index.html` (~14,800 lines) powered by Alpine.js 3.14.3 and Tailwind CSS.
- **Internationalization (i18n):** Extracted to `static/js/translations.js` (~1,350 lines), loaded globally on `window.APP_I18N`.
- **Offline & PWA:** Service Worker `static/sw.js` (Current version: `nutriagent-v90`).
- **QA Verifier:** `python verify_qa.py` — automated syntax, tag balance, `$refs`, and Alpine method checker. Always run before finishing any change.

---

## 2. Frontend Map (`static/index.html`)

### A. Document Head & Pre-load (Lines 1 – 83)
- `1-50`: Viewport, PWA meta tags, Tailwind CSS config, Google Identity Services, Alpine.js script.
- `51`: NutriAgent Translation Engine (`/static/js/translations.js`).
- `52-82`: Global styles, scrollbar styling, `[x-cloak]`.

### B. Icons & SVG Sprite (Lines 84 – 230)
- `84-230`: Stitch Clinical Duotone SVG sprite definitions (barcode scanner, meal camera, supermarket cart, fridge chef, wolt dish, water glass, etc.).

### C. Authentication & Approval Gates (Lines 231 – 370)
- `231-332`: Full-screen Google Login Gate (shown when unauthenticated).
- `333-370`: Pending Admin Approval Gate (shown for new unapproved users).

### D. Top Header & Navigation Bar (Lines 371 – 550)
- `371-400`: Logo, date display, daily streak status.
- `401-455`: Desktop segmented navigation tabs (Summary, Recipes, Analytics, Wolt, Coach Hub, Admin).
- `456-548`: Uniform Action Toolbar:
  - Language toggle (`toggleLanguage()`).
  - PWA install button.
  - Push notifications toggle.
  - Daily targets modal launcher.
  - User profile / Google sign-in pill.

### E. Main Tabs (Lines 551 – 4560)
- **Tab 1: Daily Summary & Meal Logging (`activeTab === 'summary'`)** (Lines 551 – 2800):
  - `555-740`: Coach Trainee Hub sub-view & roster cards.
  - `745-1090`: Daily Metabolic Macro Gauges (Option 1 Apple Health compact rings, minimal macro strip, classic rings).
  - `1091-1224`: Workout & Fitness Logger card (AI workout prompt, Google Fit sync).
  - `1225-1335`: AI Smart Meal Logger card (free text, WhatsApp-style voice recorder, camera preview).
  - `1336-1400`: **Fast Visual Actions Row**:
    1. Plate Camera (Direct native capture: `$refs.cameraInput.click()`).
    2. Israeli Supermarket Barcode Scanner (`openBarcodeScannerModal()`).
    3. Gallery Upload (`$refs.galleryInput.click()`).
  - `1401-1620`: Quick Voice / Text input bar.
  - `1621-1688`: Water Tracker & Quick Shake bar.
  - `1689-1830`: Coaching Mode: 5-slot structured meal flow (Breakfast, Lunch, Dinner, Snack 1, Snack 2).
  - `1831-1930`: **Empty State (`meals.length === 0`)**: direct native camera button (`$refs.cameraInput.click()`).
  - `1931-2800`: Meals Journal & Meal Cards list (delete, edit, item breakdown).
- **Tab 2: Recipes & Fridge Chef (`activeTab === 'recipes'`)** (Lines 2801 – 3070):
  - Generate recipes from fridge contents, AI pantry chef.
- **Tab 3: Analytics & Metabolic Trends (`activeTab === 'analytics'`)** (Lines 3071 – 3350):
  - Weekly macro trends, weight progress chart, calorie compliance.
- **Tab 4: B2B Partners, Leads & CRM (`activeTab === 'partners'`)** (Lines 3351 – 4080):
  - Gym / clinic partner demo, Telegram lead routing, trainer recruitment.
- **Tab 5: Admin Console (`activeTab === 'admin'`)** (Lines 3380 – 4750):
  - User management, user approvals, macro targets calibration, Telegram alerts test.
  - **Unit Economics, Infrastructure Costs & Client Pricing Studio (`adminNavTab === 'costs'`)**:
    Granular cost breakdown (Gemini Vision, Voice, Text, Cloud Run, Firestore), cost per active user, profit margin calculator, and per-user cost & pricing recommendations.
  - Button to immediately test & trigger green admin health check push.

### F. Bottom Navigation Dock & FAB (Lines 4561 – 4630)
- `4561-4630`: Fixed mobile bottom dock with Center Floating Action Button (`+` camera capture).

### G. Modals & Dialogs (Lines 4631 – 7140 & 14080 – 14800)
- `4631-5130`: Wolt Restaurant Menu & AI Dish Scanner.
- `5131-5480`: Daily Targets & Metabolic Goal Configuration Modal.
- `5481-5735`: User Profile & Trainer Linking Modal.
- `5736-5945`: Fast Food Quick-Logger / Wolt Search Modal.
- `5946-6040`: Streak & Milestone Badges Celebration Modal.
- `6041-6145`: Smart Grocery Shopping List Quick Modal.
- `6146-6225`: Edit Meal Modal.
- `6226-6385`: Workout Logger Modal (AI, manual, Google Fit, screenshot sync).
- `6386-6415`: Delete Confirmation Modal.
- `6416-6460`: PWA Installation Instructions Modal (iOS Safari / Android Chrome).
- `6461-6560`: Push Notifications Management Modal.
- `6561-6800`: **Israeli Supermarket Barcode Scanner & Search Modal** (Open Food Facts + Israeli chains).
- `6801-6835`: Web camera stream fallback modal.
- `6836-7000`: Multi-User Profile & Google OAuth Setup Modal.
- `7001-7140`: Meal Confirmation & Exact Eating Time Picker Modal (Stage 1 confirmation).
- `14080-14088`: Macro camera viewfinder placeholder & ref targets. (Full modal backed up in `static/macro_viewfinder_modal.backup.html`).
- `14089-14268`: Wolt AI Meal Customization Drawer (dynamic before/after calorie & protein deltas).
- `14269-14325`: Cool Morning Reminder Popup (direct native camera capture).
- `14326-14485`: Coach Add Trainee Modal & Holdon Affiliate Modal.
- `14486-14705`: Multi-step Onboarding & Targets Wizard.
- `14706-14800`: Terms of Service & Privacy Policy Modal.

### H. Core Alpine.js Application Logic (Lines 7141 – 14080)
- `7141-7315`: Global helper utilities:
  - `fetch` interceptor (auto-attaches `Authorization` and `X-Session-Token`).
  - Service worker registration with cache buster query.
  - `compressImageFile(file, maxDim, quality)` — client-side image compression before upload.
  - `getIsraelTimeStr()`, `getIsraelDateStr()`, `getLocalTodayStr()`.
- `7316`: `function nutritionApp() { ... }` — Main Alpine.js reactive component.
  - `7316-7500`: Reactive state definitions (`currentUser`, `meals`, `summary`, `targets`, `activeTab`, etc.).
  - `7501-7900`: Camera & Media methods (`captureMealPhoto`, `onFileSelected`, `openBarcodeScannerModal`, etc.).
  - `7901-8300`: Voice & Audio recording methods (`startRecording`, `stopRecording`, WhatsApp voice pipeline).
  - `8301-8800`: Meals CRUD (`submitTextMeal`, `deleteMeal`, `updateMeal`, `confirmMealWithTime`).
  - `8801-9400`: Wolt menu scanning, dish matching, and custom swap drawer methods.
  - `9401-9800`: Barcode scanning & Israeli supermarket database search methods.
  - `9801-10200`: Coach & Trainee management methods (`fetchTrainees`, `openTraineeDiary`).
  - `10201-10320`: Auth, Google OAuth, Push notifications, and PWA install prompt handlers.
  - `10321-10550`: `init()` method — bootstraps app, loads user session, checks morning reminder, sets up watchers.

---

## 3. Backend API Map (`bot_server.py`)

- `1-98`: Imports, logging setup, FastAPI initialization, CORS middleware.
- `99-450`: **Pydantic Data Models**:
  - `MealItemDetail`, `ManualMealRequest`, `AIMealRequest`, `PhotoMealRequest`, `VoiceMealRequest`, `TargetUpdateRequest`, `CoachTraineeCreateRequest`, etc.
- `451-4540`: Database helper routines, Gemini Vision prompts, OCR helpers, Open Food Facts parser, calorie formulas.
- `4541-4589`: Static files mount (`/static`), `/sw.js`, `/manifest.json`.
- `4590-4800`: **Auth Endpoints**:
  - `GET /api/auth/config`, `POST /api/auth/google`, `GET /api/auth/me`, `POST /api/auth/claim-legacy-data`.
- `5220-5550`: **Admin Endpoints, Unit Economics & Health Push**:
  - `GET /api/admin/billing-and-costs`: Full financial breakdown, AI usage, and unit economics per user.
  - `POST /api/admin/daily-health-and-billing-check`: Executes health check diagnostics & sends green status push to admin.
  - `_admin_daily_0800_scheduler`: Automated background task in `lifespan` executing daily at 08:00 AM Israel time.
  - `GET /api/admin/users`, `PUT /api/admin/users/{uid}/status`, `PUT /api/admin/users/{uid}/targets`.
- `5326-5430`: **Lead Capture Endpoints**:
  - `POST /api/leads/workout`, `POST /api/leads/coach`, `GET /api/admin/leads`, `POST /api/admin/test-telegram-alert`.
- `5431-5540`: **AI Vision Calibration & Logs**:
  - `GET /api/admin/vision-logs`, `POST /api/vision-logs/{id}/reanalyze`, `POST /api/admin/vision-calibrate`.
- `5541-5840`: **User Summary & Meals Queries**:
  - `GET /api/summary` (calculates daily calorie, protein, carb, fat, water totals vs targets).
  - `GET /api/meals/` (filtered by date).
- `5841-6150`: **Meal Creation & AI Analysis**:
  - `POST /api/meals/` (manual).
  - `POST /api/meals/ai` (text meal).
  - `POST /api/meals/photo` (multi-item visual breakdown with Gemini Vision).
  - `POST /api/meals/voice` (transcription + nutritional analysis).
- `6151-6260`: **Coach Trainee Management**:
  - `GET /api/coach/trainees`, `POST /api/coach/trainees`, `GET /api/coach/trainees/{id}/details`.
- `6261-6380`: **Meal Modification**:
  - `PUT /api/meals/{meal_id}`, `DELETE /api/meals/{meal_id}`.
- `6381-6550`: **Workouts & Fitness Sync**:
  - `POST /api/workouts/ai`, `POST /api/workouts/photo`, `POST /api/google-fit/sync`.
- `7050-7100`: **Favorites, Weight Tracking & Daily Targets**:
  - `GET /api/weight`, `POST /api/weight`, `PUT /api/targets`.
- `7350-7495`: **Wolt Integration**:
  - `GET /api/wolt/dishes`, `POST /api/wolt/suggest`, `POST /api/wolt/scan-menu`.
- `7496-8065`: **Barcode & Israeli Supermarket Database**:
  - `POST /api/supermarket/scan`, `GET /api/barcode/{barcode}`, `POST /api/barcode/scan-image`.
- `8066-8160`: **Recipes & Grocery Generator**:
  - `POST /api/recipes/fridge`, `POST /api/grocery/generate`.
- `8161-8270`: **Web Push Notifications**:
  - `GET /api/push/vapid-public-key`, `POST /api/push/subscribe`, `POST /api/push/test`, `POST /api/push/remind`.
- `8271-8370`: **Admin Feature Flags & WhatsApp Webhook**:
  - `PUT /api/admin/features`, `POST /api/whatsapp/webhook`.
- `8371-8487`: **Water Tracking & Onboarding**:
  - `POST /api/water/add`, `POST /api/water/set`, `POST /api/user/onboarding`.

---

## 4. Key Workflows & Code Conventions

1. **Camera Captures:**
   - Always trigger `$refs.cameraInput.click()` directly.
   - Files pass through `compressImageFile(file, 1024, 0.85)` before sending via base64 or FormData to `/api/meals/photo`.
   - Never reintroduce desktop webcams into the primary mobile empty state flow.

2. **Language & Translations:**
   - Hebrew is default (`he`), English (`en`) is fully supported.
   - Use `window.APP_I18N[currentLang][key]` for code translations, or `window.translateText(str, 'en')`.
   - All dictionary entries reside in `static/js/translations.js`.

3. **Automated Verification:**
   - After any change to `static/index.html` or `bot_server.py`, always run:
     ```bash
     python verify_qa.py
     ```
   - Must output `0 ERRORS` before declaring task complete.
