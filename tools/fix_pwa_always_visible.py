with open('static/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

# 1. Update button in Header to use !isAppInstalled
old_btn = """                <!-- 4. Direct Install PWA Button (Mobile & Desktop) -->
                <button x-cloak x-show="canInstallPWA || showIOSInstallButton"
                    type="button" @click="installPWA()"
                    class="h-8 px-2 sm:px-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-bold flex items-center gap-1 shadow-2xs transition cursor-pointer flex-shrink-0"
                    title="התקן כאפליקציה למסך הבית">
                    <span class="material-symbols-outlined text-[16px]">install_mobile</span>
                    <span>התקן</span>
                </button>"""

new_btn = """                <!-- 4. Direct Download / Install PWA Button (Always Visible when not in standalone) -->
                <button type="button" @click="installPWA()"
                    x-cloak x-show="!isAppInstalled"
                    class="h-8 px-2.5 sm:px-3 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-700 hover:to-teal-700 text-white text-xs font-black flex items-center gap-1.5 shadow-xs transition active:scale-95 cursor-pointer flex-shrink-0"
                    title="הורדת האפליקציה למסך הבית (PWA)">
                    <span class="material-symbols-outlined text-[16px]">install_mobile</span>
                    <span class="font-bold">הורד</span>
                </button>"""

assert old_btn in html, "old_btn not found"
html = html.replace(old_btn, new_btn, 1)

# 2. Add isAppInstalled and openInstallModal to Alpine state
old_state = """                canInstallPWA: false,
                showIOSInstallButton: false,
                openIOSInstallModal: false,"""

new_state = """                canInstallPWA: false,
                showIOSInstallButton: false,
                openIOSInstallModal: false,
                openInstallModal: false,
                isAppInstalled: false,"""

assert old_state in html, "old_state not found"
html = html.replace(old_state, new_state, 1)

# 3. Update setupPWAInstallListeners and installPWA methods
old_methods = """                setupPWAInstallListeners() {
                    const isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
                    if (isStandalone) return;

                    window.addEventListener('beforeinstallprompt', (e) => {
                        e.preventDefault();
                        deferredPWAPrompt = e;
                        this.canInstallPWA = true;
                    });

                    window.addEventListener('appinstalled', () => {
                        deferredPWAPrompt = null;
                        this.canInstallPWA = false;
                        this.showToast('🎉 האפליקציה הותקנה למסך הבית!');
                    });

                    const ua = window.navigator.userAgent.toLowerCase();
                    const isIOS = /iphone|ipad|ipod/.test(ua);
                    if (isIOS && !isStandalone) {
                        this.showIOSInstallButton = true;
                    }
                },

                async installPWA() {
                    if (deferredPWAPrompt) {
                        deferredPWAPrompt.prompt();
                        const { outcome } = await deferredPWAPrompt.userChoice;
                        if (outcome === 'accepted') {
                            this.canInstallPWA = false;
                        }
                        deferredPWAPrompt = null;
                    } else if (this.showIOSInstallButton) {
                        this.openIOSInstallModal = true;
                    }
                },"""

new_methods = """                setupPWAInstallListeners() {
                    const isStandalone = Boolean(
                        (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) ||
                        window.navigator.standalone === true
                    );
                    this.isAppInstalled = isStandalone;

                    window.addEventListener('beforeinstallprompt', (e) => {
                        e.preventDefault();
                        window.deferredPWAPrompt = e;
                        deferredPWAPrompt = e;
                        this.canInstallPWA = true;
                    });

                    window.addEventListener('appinstalled', () => {
                        window.deferredPWAPrompt = null;
                        deferredPWAPrompt = null;
                        this.canInstallPWA = false;
                        this.isAppInstalled = true;
                        this.showToast('🎉 האפליקציה הותקנה למסך הבית!');
                    });
                },

                async installPWA() {
                    const promptEvent = window.deferredPWAPrompt || deferredPWAPrompt;
                    if (promptEvent) {
                        try {
                            await promptEvent.prompt();
                            const choice = await promptEvent.userChoice;
                            if (choice && choice.outcome === 'accepted') {
                                this.isAppInstalled = true;
                                this.showToast('🎉 האפליקציה הותקנה למסך הבית!');
                            }
                        } catch (err) {
                            console.warn('Install prompt error:', err);
                        } finally {
                            window.deferredPWAPrompt = null;
                            deferredPWAPrompt = null;
                        }
                        return;
                    }

                    // Native prompt unavailable (iOS or Chrome manual mode) -> show simple guidance modal
                    this.openInstallModal = true;
                },"""

assert old_methods in html, "old_methods not found"
html = html.replace(old_methods, new_methods, 1)

# 4. Replace openIOSInstallModal with clean non-looping openInstallModal
old_modal = """    <!-- iOS PWA Install Instructions Modal -->
    <div x-cloak x-show="openIOSInstallModal"
         class="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-xs flex items-center justify-center p-4"
         @click.self="openIOSInstallModal = false">
        <div class="bg-white rounded-3xl shadow-2xl max-w-sm w-full p-6 space-y-4" @click.stop>
            <div class="flex items-center justify-between">
                <h3 class="text-lg font-bold text-slate-900">התקנת האפליקציה למסך הבית 📲</h3>
                <button @click="openIOSInstallModal = false" class="text-slate-400 hover:text-slate-600">✕</button>
            </div>
            <div class="space-y-3 text-sm text-slate-600 leading-relaxed">
                <p>כדי להתקין את <strong>NutriAgent</strong> כאפליקציה מלאה באייפון / אייפד:</p>
                <ol class="list-decimal list-inside space-y-2 bg-slate-50 p-4 rounded-2xl border border-slate-100">
                    <li>לחץ על כפתור ה<strong>שיתוף (Share ⬆️)</strong> בתחתית הדפדפן.</li>
                    <li>גלול ובחר ב-<strong>"הוסף למסך הבית" (Add to Home Screen ➕)</strong>.</li>
                    <li>לחץ על <strong>הוסף (Add)</strong> בפינה העליונה.</li>
                </ol>
            </div>
            <div class="flex justify-end pt-1">
                <button type="button" @click="openIOSInstallModal = false"
                        class="px-5 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold">הבנתי, תודה!</button>
            </div>
        </div>
    </div>"""

new_modal = """    <!-- Universal PWA Install Instructions Modal -->
    <div x-cloak x-show="openInstallModal || openIOSInstallModal"
         class="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-xs flex items-center justify-center p-4"
         @click.self="openInstallModal = false; openIOSInstallModal = false">
        <div class="bg-white rounded-3xl shadow-2xl max-w-sm w-full p-6 space-y-4 text-right" @click.stop>
            <div class="flex items-center justify-between border-b border-slate-100 pb-3">
                <div class="flex items-center gap-2">
                    <span class="text-2xl">📲</span>
                    <div>
                        <h3 class="text-base font-black text-slate-900">הורדת האפליקציה למכשיר</h3>
                        <p class="text-[11px] text-slate-500">שימוש מהיר ומלא במסך הבית שלך</p>
                    </div>
                </div>
                <button type="button" @click="openInstallModal = false; openIOSInstallModal = false" class="text-slate-400 hover:text-slate-700 text-xl font-bold cursor-pointer">✕</button>
            </div>

            <div class="space-y-3 text-xs text-slate-700 leading-relaxed">
                <div class="p-3.5 rounded-2xl bg-slate-50 border border-slate-200/80 space-y-2">
                    <p class="font-black text-slate-900 text-xs">🍏 באייפון / אייפד (Safari):</p>
                    <ol class="list-decimal list-inside space-y-1.5 text-slate-600 font-medium text-[11px]">
                        <li>לחץ על כפתור ה<strong>שיתוף (Share ⬆️)</strong> בתחתית המסך.</li>
                        <li>גלול מעט ובחר ב-<strong>"הוסף למסך הבית" (➕)</strong>.</li>
                        <li>לחץ על <strong>הוסף (Add)</strong> למעלה.</li>
                    </ol>
                </div>

                <div class="p-3.5 rounded-2xl bg-slate-50 border border-slate-200/80 space-y-2">
                    <p class="font-black text-slate-900 text-xs">🤖 באנדרואיד / מחשב (Chrome):</p>
                    <ol class="list-decimal list-inside space-y-1.5 text-slate-600 font-medium text-[11px]">
                        <li>לחץ על תפריט <strong>שלוש הנקודות (⋮)</strong> בראש הדפדפן.</li>
                        <li>בחר ב-<strong>"התקן אפליקציה" (Install)</strong> או <strong>"הוסף למסך דף הבית"</strong>.</li>
                        <li>אשר את ההתקנה והאייקון יופיע במסך הבית!</li>
                    </ol>
                </div>
            </div>

            <div class="pt-2 border-t border-slate-100">
                <button type="button" @click="openInstallModal = false; openIOSInstallModal = false"
                        class="w-full py-2.5 rounded-2xl bg-emerald-600 hover:bg-emerald-700 text-white font-black text-xs transition cursor-pointer shadow-sm">
                    הבנתי, תודה!
                </button>
            </div>
        </div>
    </div>"""

assert old_modal in html, "old_modal not found"
html = html.replace(old_modal, new_modal, 1)

# 5. Also add an install button on the Full-Screen Google Login Gate (when currentUser is null)
# so a user visiting the login gate can also install the app!
login_brand_target = """                    <h1 class="text-2xl font-extrabold text-slate-900 tracking-tight">NutriAgent AI</h1>"""
login_brand_install_btn = """                    <h1 class="text-2xl font-extrabold text-slate-900 tracking-tight">NutriAgent AI</h1>
                    <div class="pt-1" x-show="!isAppInstalled">
                        <button type="button" @click="installPWA()"
                                class="inline-flex items-center gap-1.5 px-3 py-1 rounded-xl bg-emerald-50 hover:bg-emerald-100 text-emerald-800 border border-emerald-200 text-xs font-bold transition cursor-pointer">
                            <span class="material-symbols-outlined text-[15px]">install_mobile</span>
                            <span>הורד כאפליקציה למסך הבית</span>
                        </button>
                    </div>"""

if login_brand_target in html and "login_brand_install_btn" not in html:
    html = html.replace(login_brand_target, login_brand_install_btn, 1)

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(html)

print("[OK] PWA install button set to always visible with non-looping direct trigger.")
