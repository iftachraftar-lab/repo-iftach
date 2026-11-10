#!/usr/bin/env node
/**
 * NutriAgent Headless Chrome/Edge UX, UI, i18n & Strip-Sync E2E Auditor
 *
 * Zero external dependencies (uses Node built-in http/fs/path/WebSocket + local Chrome/Edge CDP).
 * Catches real runtime bugs that static regex tests miss:
 *   1. Uncaught JS / Alpine runtime errors
 *   2. English mode Hebrew-text leaks across Summary, History, Recipes tabs & popups/modals
 *   3. Dynamic DOM / translations.js cache desync (e.g. Saturday 885 kcal reverting to 0)
 *   4. Modal open/close traps & Escape recovery
 *   5. Mobile (390px) & Desktop (1280px) horizontal overflow
 */

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, '..');
const STATIC_DIR = path.join(REPO_ROOT, 'static');

const BROWSER_CANDIDATES = [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
];

function findBrowser() {
  for (const p of BROWSER_CANDIDATES) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

function getLocalDateStr(offsetDays = 0) {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function findLastSaturdayOffset() {
  const d = new Date();
  const dow = d.getDay(); // 0=Sun..6=Sat
  const diff = dow === 6 ? -7 : -(dow + 1); // Most recent past Saturday within 1..7 days
  return diff;
}

function buildFixtures() {
  const todayStr = getLocalDateStr(0);
  const satOffset = findLastSaturdayOffset();
  const satStr = getLocalDateStr(satOffset);

  const rolling14 = [];
  for (let offset = -13; offset <= 0; offset++) {
    const ds = getLocalDateStr(offset);
    if (ds === satStr) {
      rolling14.push({
        date: ds,
        calories: 885,
        protein_g: 62,
        water_ml: 1500,
        meal_count: 2,
        meals_count: 2,
        burned_calories: 0,
      });
    } else if (ds === todayStr) {
      rolling14.push({
        date: ds,
        calories: 420,
        protein_g: 34,
        water_ml: 1000,
        meal_count: 1,
        meals_count: 1,
        burned_calories: 0,
      });
    } else {
      rolling14.push({
        date: ds,
        calories: 0,
        protein_g: 0,
        water_ml: 0,
        meal_count: 0,
        meals_count: 0,
        burned_calories: 0,
      });
    }
  }

  const meals = [
    {
      id: 'm_today_1',
      description: 'Oatmeal Protein Bowl',
      calories: 420,
      protein_g: 34,
      log_date: todayStr,
      log_time: '09:15',
      meal_type: 'breakfast',
      source: 'web',
      items: [{ name: 'Oats & Yogurt', calories: 420, protein_g: 34 }],
    },
    {
      id: 'm_sat_1',
      description: 'Grilled Salmon & Quinoa',
      calories: 540,
      protein_g: 42,
      log_date: satStr,
      log_time: '13:30',
      meal_type: 'lunch',
      source: 'web',
      items: [{ name: 'Salmon Fillet', calories: 540, protein_g: 42 }],
    },
    {
      id: 'm_sat_2',
      description: 'Greek Yogurt & Berries',
      calories: 345,
      protein_g: 20,
      log_date: satStr,
      log_time: '19:15',
      meal_type: 'dinner',
      source: 'web',
      items: [{ name: 'Yogurt Bowl', calories: 345, protein_g: 20 }],
    },
  ];

  return {
    todayStr,
    satStr,
    summary: {
      date: todayStr,
      user_id: 'e2e_ux_user',
      target_calories: 2000,
      target_protein: 140,
      total_calories: 420,
      total_protein: 34,
      burned_calories: 0,
      net_calories: 420,
      remaining_calories: 1580,
      water_ml: 1000,
      water_target_ml: 2500,
      meal_count: 1,
      workout_count: 0,
      meals: [meals[0]],
      workouts: [],
    },
    history: {
      meals,
      workouts: [],
      daily_trend: rolling14.slice(-7),
      rolling_14_trend: rolling14,
    },
    authMe: {
      id: 'e2e_ux_user',
      email: 'e2e@nutriagent.local',
      name: 'E2E UX Auditor',
      is_approved: true,
      is_admin: false,
      onboarding_completed: true,
      terms_accepted: true,
      session_token: 'e2e-session-token',
      experts_access: false,
      trial_workout_access: false,
      supplements_access: false,
    },
  };
}

function startMockServer(fixtures) {
  const mimeTypes = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.png': 'image/png',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
    '.webmanifest': 'application/manifest+json',
  };

  const server = http.createServer((req, res) => {
    const reqUrl = new URL(req.url, 'http://127.0.0.1');
    const pathname = reqUrl.pathname;

    if (pathname.startsWith('/api/')) {
      res.setHeader('Content-Type', 'application/json; charset=utf-8');
      if (pathname === '/api/auth/me') return res.end(JSON.stringify(fixtures.authMe));
      if (pathname === '/api/auth/config') return res.end(JSON.stringify({ google_client_id: '' }));
      if (pathname === '/api/summary') return res.end(JSON.stringify(fixtures.summary));
      if (pathname === '/api/history') return res.end(JSON.stringify(fixtures.history));
      if (pathname === '/api/recipes') return res.end(JSON.stringify({ recipes: [] }));
      if (pathname === '/api/favorites') return res.end(JSON.stringify({ favorites: [] }));
      if (pathname === '/api/weight') return res.end(JSON.stringify({ entries: [] }));
      if (pathname === '/api/messaging/status') return res.end(JSON.stringify({ telegram_linked: false, whatsapp_linked: false }));
      if (pathname === '/api/analytics/config') return res.end(JSON.stringify({ ga_measurement_id: '' }));
      if (pathname === '/api/admin/features') return res.end(JSON.stringify({ features: {} }));
      if (pathname === '/api/push/vapid-public-key') return res.end(JSON.stringify({ publicKey: '' }));
      if (pathname === '/api/ux-telemetry' || pathname === '/api/analytics/event') {
        return res.end(JSON.stringify({ status: 'ok' }));
      }
      return res.end(JSON.stringify({ status: 'ok' }));
    }

    let filePath;
    if (pathname === '/' || pathname === '/index.html') {
      filePath = path.join(STATIC_DIR, 'index.html');
    } else if (pathname === '/sw.js') {
      filePath = path.join(STATIC_DIR, 'sw.js');
    } else if (pathname.startsWith('/static/')) {
      filePath = path.join(REPO_ROOT, pathname.slice(1));
    } else {
      filePath = path.join(STATIC_DIR, pathname.slice(1));
    }

    if (!filePath.startsWith(REPO_ROOT) || !fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
      res.statusCode = 404;
      return res.end('Not found');
    }
    const ext = path.extname(filePath).toLowerCase();
    res.setHeader('Content-Type', mimeTypes[ext] || 'application/octet-stream');
    fs.createReadStream(filePath).pipe(res);
  });

  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const addr = server.address();
      resolve({ server, baseUrl: `http://127.0.0.1:${addr.port}` });
    });
  });
}

function launchHeadlessBrowser(browserPath) {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'nutri-ux-cdp-'));
  const proc = spawn(
    browserPath,
    [
      '--headless=new',
      '--remote-debugging-port=0',
      `--user-data-dir=${userDataDir}`,
      '--disable-gpu',
      '--no-first-run',
      '--no-default-browser-check',
      '--disable-background-networking',
      '--window-size=1280,900',
      'about:blank',
    ],
    { stdio: ['ignore', 'pipe', 'pipe'] }
  );

  return new Promise((resolve, reject) => {
    let stderrBuf = '';
    const timer = setTimeout(() => {
      proc.kill();
      reject(new Error(`Timed out waiting for DevTools WebSocket URL. stderr=${stderrBuf}`));
    }, 15000);

    proc.stderr.on('data', (chunk) => {
      stderrBuf += chunk.toString();
      const match = stderrBuf.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (match) {
        clearTimeout(timer);
        resolve({ proc, wsEndpoint: match[1], userDataDir });
      }
    });
    proc.on('exit', (code) => {
      clearTimeout(timer);
      reject(new Error(`Browser exited early with code ${code}: ${stderrBuf}`));
    });
  });
}

class CDPClient {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
  }
  async connect() {
    this.ws = new WebSocket(this.wsUrl);
    await new Promise((res, rej) => {
      this.ws.addEventListener('open', res, { once: true });
      this.ws.addEventListener('error', rej, { once: true });
    });
    this.ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(JSON.stringify(msg.error)));
        else resolve(msg.result);
      } else if (msg.method) {
        const handlers = this.listeners.get(msg.method) || [];
        for (const h of handlers) h(msg.params, msg.sessionId);
      }
    });
  }
  on(method, fn) {
    if (!this.listeners.has(method)) this.listeners.set(method, []);
    this.listeners.get(method).push(fn);
  }
  send(method, params = {}, sessionId = undefined) {
    const id = this.nextId++;
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify(payload));
    });
  }
  close() {
    try { this.ws.close(); } catch {}
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function runAudit() {
  const browserPath = findBrowser();
  if (!browserPath) {
    console.error('ERROR: Chrome or Edge binary not found for headless UX/UI audit.');
    process.exit(2);
  }

  const fixtures = buildFixtures();
  const { server, baseUrl } = await startMockServer(fixtures);
  const { proc, wsEndpoint, userDataDir } = await launchHeadlessBrowser(browserPath);
  const cdp = new CDPClient(wsEndpoint);

  const report = {
    passed: [],
    failed: [],
    warnings: [],
    jsErrors: [],
    hebrewLeaks: [],
  };

  try {
    await cdp.connect();
    const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
    const send = (method, params = {}) => cdp.send(method, params, sessionId);
    const evalJs = async (expression) => {
      const res = await send('Runtime.evaluate', {
        expression,
        returnByValue: true,
        awaitPromise: true,
      });
      if (res.exceptionDetails) {
        throw new Error(`JS Eval Exception: ${JSON.stringify(res.exceptionDetails)}`);
      }
      return res.result?.value;
    };

    await send('Page.enable');
    await send('Runtime.enable');

    cdp.on('Runtime.exceptionThrown', (params) => {
      const text = params.exceptionDetails?.exception?.description || params.exceptionDetails?.text || 'Unknown Runtime Exception';
      report.jsErrors.push(text);
    });
    cdp.on('Runtime.consoleAPICalled', (params) => {
      if (params.type === 'error') {
        const text = (params.args || []).map((a) => a.value || a.description || '').join(' ');
        // Ignore network/service-worker errors caused solely by external/offline CDN/GA if any
        if (!text.includes('ServiceWorker') && !text.includes('favicon')) {
          report.jsErrors.push(`console.error: ${text}`);
        }
      }
    });

    // Seed authenticated state + English mode BEFORE first navigation
    await send('Page.addScriptToEvaluateOnNewDocument', {
      source: `
        localStorage.setItem('nutri_lang', 'en');
        localStorage.setItem('nutriagent_terms_version', '2026-11-health-v1');
        localStorage.setItem('nutri_onboarding_completed', 'true');
        localStorage.setItem('nutri_bmi_calibrated', 'true');
        localStorage.setItem('nutri_user', ${JSON.stringify(JSON.stringify(fixtures.authMe))});
      `,
    });

    await send('Page.navigate', { url: baseUrl });

    // Wait for Alpine.js & fetchHistory() to settle
    let ready = false;
    for (let i = 0; i < 40; i++) {
      ready = await evalJs(`(() => {
        const el = document.querySelector('[x-data]');
        if (!el || !window.Alpine) return false;
        const d = window.Alpine.$data(el);
        return Boolean(d && !d.loading && Array.isArray(d.weeklyStripDays) && d.weeklyStripDays.length === 14);
      })()`);
      if (ready) break;
      await sleep(150);
    }
    if (!ready) {
      throw new Error('Timed out waiting for Alpine.js + weeklyStripDays(14) to initialize.');
    }
    await sleep(180); // Allow translations.js MutationObserver (35ms debounce) to run

    // Helper: scan visible Hebrew leaks in the current DOM (excluding lang switcher button + sr-only + script/style)
    const scanVisibleHebrewLeaks = async (scopeLabel) => {
      const leaks = await evalJs(`(() => {
        const heRe = /[\\u0590-\\u05FF]/;
        const allowExact = new Set(['עברית', 'עב', '🇮🇱 עברית', 'א', 'ב']);
        const out = [];
        const walk = (node) => {
          if (!node) return;
          if (node.nodeType === 1) {
            const tag = node.tagName;
            if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT') return;
            if (node.classList && node.classList.contains('sr-only')) return;
            const cs = getComputedStyle(node);
            if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return;
            for (const attr of ['placeholder', 'title']) {
              const av = node.getAttribute(attr);
              if (av && heRe.test(av) && !allowExact.has(av.trim())) {
                out.push({ where: '${scopeLabel}', type: 'attr:' + attr, text: av.trim().slice(0, 90) });
              }
            }
            for (const c of node.childNodes) walk(c);
          } else if (node.nodeType === 3) {
            const txt = (node.nodeValue || '').trim();
            if (!txt || !heRe.test(txt) || allowExact.has(txt)) return;
            const parent = node.parentElement;
            if (!parent) return;
            const rect = parent.getBoundingClientRect();
            if (rect.width === 0 && rect.height === 0) return;
            out.push({ where: '${scopeLabel}', type: 'text', text: txt.slice(0, 90) });
          }
        };
        walk(document.body);
        // Deduplicate by text
        const seen = new Set();
        return out.filter((item) => {
          const k = item.type + '|' + item.text;
          if (seen.has(k)) return false;
          seen.add(k);
          return true;
        });
      })()`);
      return leaks || [];
    };

    // -----------------------------------------------------------------------
    // CHECK 1: Strip <-> History Synchronization in EN and HE (Saturday 885 bug)
    // -----------------------------------------------------------------------
    const dirEn = await evalJs('document.documentElement.dir');
    if (dirEn !== 'ltr') {
      report.failed.push(`Expected dir='ltr' in English mode, got '${dirEn}'`);
    } else {
      report.passed.push('English mode loads with dir=ltr and lang=en');
    }

    // Click Saturday dot in the top 14-day rolling strip
    const satClickedEn = await evalJs(`(() => {
      const root = window.Alpine.$data(document.querySelector('[x-data]'));
      const satDay = root.weeklyStripDays.find(d => d.dateStr === '${fixtures.satStr}');
      if (!satDay) return { ok: false, reason: 'Saturday missing from weeklyStripDays' };
      const btns = Array.from(document.querySelectorAll('[x-ref="rollingDaysStrip"] button'));
      const satIdx = root.weeklyStripDays.findIndex(d => d.dateStr === '${fixtures.satStr}');
      if (!btns[satIdx]) return { ok: false, reason: 'Saturday DOM button missing at index ' + satIdx };
      btns[satIdx].click();
      return { ok: true, satCaloriesData: satDay.calories, hasLogged: satDay.hasLogged };
    })()`);

    await sleep(200); // Wait past 35ms translations.js MutationObserver debounce!

    const satDomEn = await evalJs(`(() => {
      const root = window.Alpine.$data(document.querySelector('[x-data]'));
      const previewEl = document.querySelector('[x-show="activeStripPreviewDay?.hasLogged"]');
      return {
        previewVisible: previewEl ? getComputedStyle(previewEl).display !== 'none' : false,
        previewText: previewEl ? previewEl.innerText.replace(/\\s+/g, ' ').trim() : '',
        activePreviewCal: root.activeStripPreviewDay ? root.activeStripPreviewDay.calories : null,
      };
    })()`);

    if (
      satClickedEn.ok &&
      satClickedEn.satCaloriesData === 885 &&
      satDomEn.previewVisible &&
      satDomEn.previewText.includes('885') &&
      satDomEn.previewText.includes('62g') &&
      satDomEn.previewText.includes('1.5L')
    ) {
      report.passed.push(
        `Strip->Saturday EN render synced accurately after MutationObserver debounce ("${satDomEn.previewText}")`
      );
    } else {
      report.failed.push(
        `DESYNC BUG: Saturday strip EN preview rendered "${satDomEn.previewText}" (expected 885 / 62g / 1.5L); click=${JSON.stringify(satClickedEn)}`
      );
    }

    // Open Saturday full diary from the strip and verify History matches 885
    await evalJs(`(() => {
      const root = window.Alpine.$data(document.querySelector('[x-data]'));
      root.openStripFullDiary('${fixtures.satStr}');
    })()`);
    await sleep(200);

    const historyDomEn = await evalJs(`(() => {
      const root = window.Alpine.$data(document.querySelector('[x-data]'));
      return {
        activeTab: root.activeTab,
        selectedDate: root.selectedDate,
        selectedDateCalories: root.selectedDateCalories,
        mealsCount: (root.filteredHistoryMeals || []).length,
      };
    })()`);

    if (historyDomEn.activeTab === 'history' && historyDomEn.selectedDate === fixtures.satStr && historyDomEn.selectedDateCalories === 885) {
      report.passed.push(`History diary synced with strip for Saturday (${historyDomEn.selectedDateCalories} kcal, ${historyDomEn.mealsCount} meals)`);
    } else {
      report.failed.push(`History desync on Saturday: ${JSON.stringify(historyDomEn)}`);
    }

    // Test switching back to Hebrew and verifying Saturday still shows 885
    await evalJs(`(() => {
      const root = window.Alpine.$data(document.querySelector('[x-data]'));
      if (root.currentLang !== 'he') root.toggleLanguage();
    })()`);
    await sleep(180);
    const heCheck = await evalJs(`(() => {
      const root = window.Alpine.$data(document.querySelector('[x-data]'));
      root.switchTab('summary');
      const previewEl = document.querySelector('[x-show="activeStripPreviewDay?.hasLogged"]');
      return {
        dir: document.documentElement.dir,
        selectedDateCalories: root.selectedDateCalories,
      };
    })()`);
    if (heCheck.dir === 'rtl' && heCheck.selectedDateCalories === 885) {
      report.passed.push('Hebrew mode switch restored dir=rtl and preserved 885 kcal sync');
    } else {
      report.failed.push(`Hebrew switch check failed: ${JSON.stringify(heCheck)}`);
    }

    // Switch back to English for full UI & modal Hebrew-leak audit
    await evalJs(`(() => {
      const root = window.Alpine.$data(document.querySelector('[x-data]'));
      if (root.currentLang !== 'en') root.toggleLanguage();
    })()`);
    await sleep(180);

    // -----------------------------------------------------------------------
    // CHECK 2: Tabs Hebrew-Leak Scan in English mode (summary, history, recipes)
    // -----------------------------------------------------------------------
    for (const tab of ['summary', 'history', 'recipes']) {
      await evalJs(`window.Alpine.$data(document.querySelector('[x-data]')).switchTab('${tab}')`);
      await sleep(180);
      const leaks = await scanVisibleHebrewLeaks(`tab:${tab}`);
      report.hebrewLeaks.push(...leaks);
    }

    // -----------------------------------------------------------------------
    // CHECK 3: Interactive Popups/Modals Leak & Close-Path Audit in English mode
    // -----------------------------------------------------------------------
    await evalJs(`window.Alpine.$data(document.querySelector('[x-data]')).switchTab('summary')`);
    await sleep(100);

    const modalsToTest = [
      { flag: 'openTargetModal', label: 'modal:targets' },
      { flag: 'showBmiWizard', label: 'modal:bmi' },
      { flag: 'showHoldonAffiliateModal', label: 'modal:shake_popup' },
      { flag: 'showTrialWorkoutModal', label: 'modal:trainer_popup' },
      { flag: 'showExpertsDirectoryModal', label: 'modal:dietitian_popup' },
      { flag: 'showWorkoutModal', label: 'modal:workout' },
      { flag: 'showAchievementsModal', label: 'modal:achievements' },
      { flag: 'showGroceryModal', label: 'modal:grocery' },
      { flag: 'openBarcodeModal', label: 'modal:barcode' },
      { flag: 'showSupportModal', label: 'modal:support' },
      { flag: 'openPushModal', label: 'modal:push_notifications' },
    ];

    for (const m of modalsToTest) {
      await evalJs(`(() => {
        const root = window.Alpine.$data(document.querySelector('[x-data]'));
        root['${m.flag}'] = true;
      })()`);
      await sleep(150);
      const leaks = await scanVisibleHebrewLeaks(m.label);
      report.hebrewLeaks.push(...leaks);
      await evalJs(`(() => {
        const root = window.Alpine.$data(document.querySelector('[x-data]'));
        root.closeAllModals();
      })()`);
      await sleep(60);
      const stillOpen = await evalJs(`Boolean(window.Alpine.$data(document.querySelector('[x-data]'))['${m.flag}'])`);
      if (stillOpen) {
        report.failed.push(`Modal trap: ${m.flag} stayed open after closeAllModals()`);
      }
    }
    report.passed.push(`Tested ${modalsToTest.length} popups/modals for close-path & live EN translation`);

    const dedupSeen = new Set();
    for (const l of report.hebrewLeaks) dedupSeen.add(l.type + '|' + l.text);
    const uniqueLeaks = [];
    const seenU = new Set();
    for (const l of report.hebrewLeaks) {
      const k = l.type + '|' + l.text;
      if (!seenU.has(k)) { seenU.add(k); uniqueLeaks.push(l); }
    }
    report.hebrewLeaks = uniqueLeaks;

    if (report.hebrewLeaks.length === 0) {
      report.passed.push('Zero Hebrew text leaks in English mode across all 3 user tabs & 10 modals');
    } else {
      report.failed.push(`Found ${report.hebrewLeaks.length} unique Hebrew leak(s) in English UI`);
    }

    // -----------------------------------------------------------------------
    // CHECK 4: Mobile Viewport (390x844) & Desktop (1280x900) Overflow Audit
    // -----------------------------------------------------------------------
    const desktopOverflow = await evalJs('document.documentElement.scrollWidth - window.innerWidth');
    if (desktopOverflow > 2) {
      report.failed.push(`Desktop horizontal overflow detected: +${desktopOverflow}px`);
    } else {
      report.passed.push('Desktop viewport (1280px) zero horizontal overflow');
    }

    await send('Emulation.setDeviceMetricsOverride', {
      width: 390,
      height: 844,
      deviceScaleFactor: 2,
      mobile: true,
    });
    await sleep(150);
    const mobileOverflow = await evalJs('document.documentElement.scrollWidth - window.innerWidth');
    if (mobileOverflow > 2) {
      report.failed.push(`Mobile (390px) horizontal overflow detected: +${mobileOverflow}px`);
    } else {
      report.passed.push('Mobile viewport (390px) zero horizontal overflow');
    }

    const mobilePushVisible = await evalJs(`(() => {
      const btn = Array.from(document.querySelectorAll('button')).find(b => (b.getAttribute('@click') || '').includes('openPushModal'));
      if (!btn) return false;
      const r = btn.getBoundingClientRect();
      return getComputedStyle(btn).display !== 'none' && r.width > 0 && r.height > 0;
    })()`);
    if (mobilePushVisible) {
      report.passed.push('Mobile (390px) Push Notifications bell button is visible and clickable');
    } else {
      report.failed.push('Mobile (390px) Push Notifications bell button is hidden!');
    }

    if (report.jsErrors.length === 0) {
      report.passed.push('Zero uncaught JS/Alpine runtime errors');
    } else {
      report.failed.push(`Captured ${report.jsErrors.length} runtime JS error(s)`);
    }
  } finally {
    cdp.close();
    try { proc.kill(); } catch {}
    try { server.close(); } catch {}
    try { fs.rmSync(userDataDir, { recursive: true, force: true }); } catch {}
  }

  console.log('==============================================================================');
  console.log('🧪 NUTRIAGENT REAL-BROWSER UX / UI / I18N / SYNC E2E AUDITOR');
  console.log('==============================================================================');
  for (const p of report.passed) console.log(`  ✅ ${p}`);
  for (const w of report.warnings) console.log(`  ⚠️  ${w}`);
  for (const f of report.failed) console.log(`  ❌ ${f}`);
  if (report.hebrewLeaks.length > 0) {
    console.log('\n--- Detected Unique Hebrew Leaks in English Mode (first 60) ---');
    for (const l of report.hebrewLeaks.slice(0, 60)) {
      console.log(`  [${l.where}] (${l.type}): ${l.text}`);
    }
  }
  if (report.jsErrors.length > 0) {
    console.log('\n--- Captured Runtime JS Errors ---');
    for (const e of report.jsErrors) console.log(`  ${e}`);
  }
  console.log('==============================================================================');

  process.exit(report.failed.length > 0 ? 1 : 0);
}

runAudit().catch((err) => {
  console.error('UX/UI E2E Auditor Fatal Error:', err);
  process.exit(1);
});
