with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update the Header PWA Install Button to be permanently visible when !isStandalone (both mobile & desktop)
old_pwa_btn = """                <!-- 4. Install PWA (when available) -->
                <button x-cloak x-show="canInstallPWA || showIOSInstallButton"
                    type="button" @click="installPWA()"
                    class="hidden md:flex h-8 px-2 sm:px-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-bold items-center gap-1 shadow-2xs transition cursor-pointer flex-shrink-0"
                    title="התקן כאפליקציה למסך הבית">
                    <span class="material-symbols-outlined text-[16px]">install_mobile</span>
                    <span class="hidden md:inline">התקן</span>
                </button>"""

new_pwa_btn = """                <!-- 4. Install PWA (Visible on Mobile & Desktop) -->
                <button x-cloak x-show="!isStandalone"
                    type="button" @click="installPWA()"
                    class="h-8 px-2 sm:px-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-black flex items-center gap-1 shadow-xs transition active:scale-95 cursor-pointer flex-shrink-0"
                    title="התקן כאפליקציה למסך הבית (PWA)">
                    <span class="material-symbols-outlined text-[16px]">install_mobile</span>
                    <span class="hidden sm:inline">התקן אפליקציה</span>
                    <span class="sm:hidden font-bold">התקן</span>
                </button>"""

assert old_pwa_btn in content, "old_pwa_btn not found!"
content = content.replace(old_pwa_btn, new_pwa_btn, 1)

# 2. Add isStandalone & pwaInstallTab to data
old_data_snippet = """                canInstallPWA: false,
                showIOSInstallButton: false,
                openIOSInstallModal: false,"""

new_data_snippet = """                canInstallPWA: false,
                showIOSInstallButton: false,
                openIOSInstallModal: false,
                isStandalone: false,
                pwaInstallTab: 'auto',"""

assert old_data_snippet in content, "old_data_snippet not found!"
content = content.replace(old_data_snippet, new_data_snippet, 1)

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
                    const isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
                    this.isStandalone = Boolean(isStandalone);
                    const ua = window.navigator.userAgent.toLowerCase();
                    const isIOS = /iphone|ipad|ipod/.test(ua);
                    this.pwaInstallTab = isIOS ? 'ios' : 'android';
                    if (isIOS && !isStandalone) {
                        this.showIOSInstallButton = true;
                    }

                    window.addEventListener('beforeinstallprompt', (e) => {
                        e.preventDefault();
                        deferredPWAPrompt = e;
                        this.canInstallPWA = true;
                    });

                    window.addEventListener('appinstalled', () => {
                        deferredPWAPrompt = null;
                        this.canInstallPWA = false;
                        this.isStandalone = true;
                        this.showToast('🎉 האפליקציה הותקנה בהצלחה למסך הבית!');
                    });
                },

                async installPWA() {
                    if (deferredPWAPrompt) {
                        try {
                            deferredPWAPrompt.prompt();
                            const { outcome } = await deferredPWAPrompt.userChoice;
                            if (outcome === 'accepted') {
                                this.canInstallPWA = false;
                                this.isStandalone = true;
                            }
                        } catch (e) {
                            console.error('PWA prompt error:', e);
                            this.openIOSInstallModal = true;
                        }
                        deferredPWAPrompt = null;
                    } else {
                        const ua = window.navigator.userAgent.toLowerCase();
                        this.pwaInstallTab = /iphone|ipad|ipod/.test(ua) ? 'ios' : 'android';
                        this.openIOSInstallModal = true;
                    }
                },"""

assert old_methods in content, "old_methods not found!"
content = content.replace(old_methods, new_methods, 1)

# 4. Update the Install Modal to be Universal (iOS & Android)
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

new_modal = """    <!-- Universal PWA Install Instructions Modal (iOS & Android) -->
    <div x-cloak x-show="openIOSInstallModal"
         class="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-xs flex items-center justify-center p-4"
         @click.self="openIOSInstallModal = false">
        <div class="bg-white rounded-3xl shadow-2xl max-w-sm w-full p-6 space-y-4 text-right" @click.stop>
            <div class="flex items-center justify-between border-b border-slate-100 pb-3">
                <div class="flex items-center gap-2">
                    <div class="w-10 h-10 rounded-2xl bg-emerald-50 text-emerald-600 flex items-center justify-center text-xl shadow-xs">
                        📲
                    </div>
                    <div>
                        <h3 class="text-base font-black text-slate-900">התקנת NutriAgent לנייד</h3>
                        <p class="text-[11px] text-slate-500">שימוש מהיר כמו אפליקציה רגילה מחנות האפליקציות</p>
                    </div>
                </div>
                <button type="button" @click="openIOSInstallModal = false" class="text-slate-400 hover:text-slate-600 text-xl font-bold cursor-pointer">✕</button>
            </div>

            <!-- Fast 1-tap install if native browser prompt is available -->
            <div x-show="canInstallPWA" class="p-3 bg-emerald-50 rounded-2xl border border-emerald-200 text-center space-y-2">
                <p class="text-xs font-bold text-emerald-950">הדפדפן שלך תומך בהתקנה מיידית בטאפ אחד:</p>
                <button type="button" @click="installPWA()"
                        class="w-full py-2.5 px-4 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white font-black text-xs shadow-md transition cursor-pointer">
                    ⚡ התקן עכשיו למסך הבית
                </button>
            </div>

            <!-- Platform Switcher Tabs (iOS / Android) -->
            <div class="flex items-center gap-1.5 p-1 bg-slate-100 rounded-2xl text-xs font-bold">
                <button type="button" @click="pwaInstallTab = 'ios'"
                        class="flex-1 py-1.5 rounded-xl transition cursor-pointer flex items-center justify-center gap-1"
                        :class="pwaInstallTab === 'ios' ? 'bg-white text-slate-900 shadow-2xs' : 'text-slate-600 hover:text-slate-900'">
                    <span>🍏</span>
                    <span>אייפון (iOS)</span>
                </button>
                <button type="button" @click="pwaInstallTab = 'android'"
                        class="flex-1 py-1.5 rounded-xl transition cursor-pointer flex items-center justify-center gap-1"
                        :class="pwaInstallTab === 'android' ? 'bg-white text-slate-900 shadow-2xs' : 'text-slate-600 hover:text-slate-900'">
                    <span>🤖</span>
                    <span>אנדרואיד / Chrome</span>
                </button>
            </div>

            <!-- Tab 1: iOS Instructions -->
            <div x-show="pwaInstallTab === 'ios'" class="space-y-2.5 text-xs text-slate-700 leading-relaxed">
                <p class="font-bold text-slate-900">הוראות התקנה באייפון / אייפד (Safari):</p>
                <ol class="list-decimal list-inside space-y-2 bg-slate-50 p-3.5 rounded-2xl border border-slate-200/80 font-medium">
                    <li>לחץ על כפתור ה<strong>שיתוף (Share ⬆️)</strong> בתחתית המסך.</li>
                    <li>גלול ובחר ב-<strong>"הוסף למסך הבית" (Add to Home Screen ➕)</strong>.</li>
                    <li>לחץ על <strong>"הוסף" (Add)</strong> בפינה העליונה.</li>
                </ol>
            </div>

            <!-- Tab 2: Android Instructions -->
            <div x-show="pwaInstallTab === 'android'" class="space-y-2.5 text-xs text-slate-700 leading-relaxed">
                <p class="font-bold text-slate-900">הוראות התקנה באנדרואיד (Chrome / דפדפן):</p>
                <ol class="list-decimal list-inside space-y-2 bg-slate-50 p-3.5 rounded-2xl border border-slate-200/80 font-medium">
                    <li>לחץ על תפריט <strong>שלוש הנקודות (⋮)</strong> בפינה העליונה של הדפדפן.</li>
                    <li>בחר באפשרות <strong>"התקן אפליקציה" (Install App)</strong> או <strong>"הוסף למסך דף הבית"</strong>.</li>
                    <li>אשר את ההתקנה – האייקון יופיע ישירות על מסך הבית שלך!</li>
                </ol>
            </div>

            <div class="flex justify-end pt-2 border-t border-slate-100">
                <button type="button" @click="openIOSInstallModal = false"
                        class="w-full py-2.5 rounded-2xl bg-slate-900 hover:bg-slate-800 text-white text-xs font-black transition cursor-pointer">
                    הבנתי, תודה!
                </button>
            </div>
        </div>
    </div>"""

assert old_modal in content, "old_modal not found!"
content = content.replace(old_modal, new_modal, 1)

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("[OK] PWA install button and modal updated successfully.")
