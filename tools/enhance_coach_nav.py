import re

with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Desktop Nav: Add 'מתאמנים ובוט' button right before admin button in desktop nav
desktop_nav_target = """                <button x-cloak x-show="currentUser && currentUser.is_admin"
                    @click="switchTab('admin')\""""

desktop_nav_btn = """                <!-- Coach / Trainees Hub Tab for Coach & Admin -->
                <button x-cloak x-show="isCoachOrAdmin()"
                    @click="openTraineesTab()"
                    :class="(activeTab === 'summary' && coachingSubTab === 'trainees') ? 'bg-indigo-600 text-white shadow-2xs font-bold' : 'text-indigo-700 hover:bg-indigo-50 font-semibold'"
                    class="h-8 px-3 rounded-lg text-xs transition-all flex items-center gap-1.5 cursor-pointer"
                    title="ניהול רשימת המתאמנים של איתמר והפקת קישורי שיתוף לטלגרם / וואטסאפ">
                    <span>👥</span>
                    <span>מתאמנים ובוט</span>
                    <span class="px-1.5 py-0.5 rounded-full text-[10px] font-bold"
                          :class="(activeTab === 'summary' && coachingSubTab === 'trainees') ? 'bg-white/20 text-white' : 'bg-indigo-100 text-indigo-800'"
                          x-text="trainees.length"></span>
                </button>
"""

if "openTraineesTab()" not in content:
    assert desktop_nav_target in content, "desktop_nav_target not found!"
    content = content.replace(desktop_nav_target, desktop_nav_btn + desktop_nav_target, 1)

# 2. Header Action Toolbar: Add Quick '➕ הפק קישור למתאמן' button
toolbar_target = """            <!-- Uniform Action Toolbar (Clean, Spaced & Uncluttered) -->
            <div class="flex items-center gap-1.5 sm:gap-2 flex-shrink-0 flex-nowrap">"""

toolbar_btn = """
                <!-- Fast Trainee Link Generation for Coach/Admin -->
                <button x-cloak x-show="isCoachOrAdmin()"
                    type="button"
                    @click="showAddTraineeModal = true"
                    class="h-8 px-2.5 sm:px-3 rounded-xl bg-gradient-to-r from-emerald-500 to-teal-600 hover:from-emerald-600 hover:to-teal-700 text-white text-xs font-black shadow-xs flex items-center gap-1.5 cursor-pointer transition active:scale-95 flex-shrink-0"
                    title="הפק קישור הצטרפות אישי לטלגרם / וואטסאפ למתאמן חדש">
                    <span>➕</span>
                    <span class="hidden sm:inline">הפק קישור למתאמן</span>
                    <span class="sm:hidden font-bold">קישור</span>
                </button>"""

if "הפק קישור למתאמן" not in content:
    assert toolbar_target in content, "toolbar_target not found!"
    content = content.replace(toolbar_target, toolbar_target + toolbar_btn, 1)

# 3. Mobile Bottom Nav: Add Trainees button before mobile admin button
mobile_nav_target = """            <button x-cloak x-show="currentUser && currentUser.is_admin"
                    @click="switchTab('admin')\""""

mobile_nav_btn = """            <button x-cloak x-show="isCoachOrAdmin()"
                    @click="openTraineesTab()"
                    :class="(activeTab === 'summary' && coachingSubTab === 'trainees') ? 'text-indigo-600 font-bold' : 'text-slate-500 hover:text-slate-800'"
                    class="flex-1 py-1 flex flex-col items-center justify-center transition cursor-pointer">
                <span class="text-lg leading-none mb-0.5">👥</span>
                <span class="text-[10px]">מתאמנים</span>
            </button>
"""

if "openTraineesTab()" in content and mobile_nav_target in content:
    content = content.replace(mobile_nav_target, mobile_nav_btn + mobile_nav_target, 1)

# 4. Admin Users Tab: Add High-Visibility Card for Trainee Bot Link Generation
admin_card_target = """            <!-- Pre-Add / Invite User Card -->"""

admin_card_markup = """            <!-- COACHING QUICK LINK & TRAINEES HUB BANNER (Admin View) -->
            <div x-show="adminNavTab === 'users'" class="bg-gradient-to-r from-emerald-600 via-teal-600 to-indigo-700 rounded-3xl p-5 sm:p-6 text-white shadow-lg space-y-3 relative overflow-hidden">
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4 relative z-10">
                    <div class="space-y-1">
                        <div class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-white/20 text-white text-[11px] font-bold">
                            <span>🤖</span>
                            <span>חיבור מתאמן לבוט טלגרם / וואטסאפ</span>
                        </div>
                        <h3 class="text-base sm:text-lg font-black">הפקת קישור אישי מהמאמן למתאמן</h3>
                        <p class="text-xs text-emerald-100 max-w-xl">
                            בלחיצה אחת: מפיקים קישור deep-link אישי לבוט הטלגרם של איתמר (<span class="font-mono bg-black/20 px-1 py-0.5 rounded">@Iftachtaskbot</span>) וטקסט הזמנה לוואטסאפ. כשהמתאמן לוחץ על הלינק, הוא מחובר מיד לחשבון שלו וכל ארוחה מתועדת ביומן!
                        </p>
                    </div>
                    <div class="flex items-center gap-2 flex-wrap">
                        <button type="button" @click="showAddTraineeModal = true"
                                class="px-4 py-2.5 rounded-2xl bg-white hover:bg-emerald-50 text-emerald-950 font-black text-xs transition cursor-pointer shadow-md flex items-center gap-1.5 active:scale-95">
                            <span>➕</span>
                            <span>הוסף מתאמן והפק קישור ➔</span>
                        </button>
                        <button type="button" @click="openTraineesTab()"
                                class="px-3.5 py-2.5 rounded-2xl bg-white/10 hover:bg-white/20 border border-white/30 text-white font-bold text-xs transition cursor-pointer flex items-center gap-1.5">
                            <span>👥</span>
                            <span>לוח מתאמנים של איתמר (<span x-text="trainees.length"></span>)</span>
                        </button>
                    </div>
                </div>
            </div>

"""

if "COACHING QUICK LINK & TRAINEES HUB BANNER" not in content:
    assert admin_card_target in content, "admin_card_target not found!"
    content = content.replace(admin_card_target, admin_card_markup + admin_card_target, 1)

# 5. Alpine openTraineesTab helper
alpine_target = """            isCoachOrAdmin() {"""
alpine_helper = """            openTraineesTab() {
                this.appMode = 'coaching';
                this.activeTab = 'summary';
                this.coachingSubTab = 'trainees';
                this.resetViewingTrainee();
                this.fetchCoachTrainees();
            },
"""

if "openTraineesTab() {" not in content:
    assert alpine_target in content, "alpine_target not found!"
    content = content.replace(alpine_target, alpine_helper + alpine_target, 1)

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("[OK] Enhanced coach navigation successfully applied.")
