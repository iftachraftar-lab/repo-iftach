# 2. Update static/index.html
with open('static/index.html', 'r', encoding='utf-8') as f:
    html_code = f.read()

# Update fetchVisionLogs
old_fetch = """                async fetchVisionLogs() {
                    if (!this.currentUser) return;
                    this.visionLoading = true;
                    try {
                        const endpoint = this.currentUser.is_admin ? '/api/admin/vision-logs?limit=150' : '/api/meals/photo-history?limit=150';
                        const res = await fetch(endpoint);
                        if (!res.ok) return;
                        const data = await res.json();
                        this.visionLogs = data.logs || [];
                        this.visionCalibrationRules = data.calibration_rules || [];
                        this.visionStats = data.stats || null;
                    } catch (e) {
                        console.error('fetchVisionLogs error:', e);
                    } finally {
                        this.visionLoading = false;
                    }
                },"""

new_fetch = """                async fetchVisionLogs() {
                    if (!this.currentUser) return;
                    this.visionLoading = true;
                    try {
                        const isItamar = (this.currentUser.email || '').toLowerCase().includes('itamar');
                        const limitParam = isItamar ? 15 : (this.currentUser.is_admin ? 150 : 150);
                        const endpoint = this.currentUser.is_admin ? `/api/admin/vision-logs?limit=${limitParam}` : `/api/meals/photo-history?limit=${limitParam}`;
                        const res = await fetch(endpoint);
                        if (!res.ok) return;
                        const data = await res.json();
                        let logs = data.logs || [];
                        if (isItamar) {
                            logs = logs.slice(0, 15);
                        }
                        this.visionLogs = logs;
                        this.visionCalibrationRules = data.calibration_rules || [];
                        this.visionStats = data.stats || null;
                        if (isItamar && this.visionStats && this.visionStats.total_photos > 15) {
                            this.visionStats.total_photos = 15;
                        }
                    } catch (e) {
                        console.error('fetchVisionLogs error:', e);
                    } finally {
                        this.visionLoading = false;
                    }
                },"""

if old_fetch in html_code:
    html_code = html_code.replace(old_fetch, new_fetch, 1)

# Update filteredVisionLogs
old_filtered = """                get filteredVisionLogs() {
                    const q = (this.visionSearchQuery || '').trim().toLowerCase();
                    const flt = this.visionFilter || 'all';
                    return (this.visionLogs || []).filter(vlog => {
                        if (flt === 'meals' && !(vlog.ai_analysis && vlog.ai_analysis.is_meal)) return false;
                        if (flt === 'enhanced' && !(vlog.forensics && vlog.forensics.enhancements_applied && vlog.forensics.enhancements_applied.length > 0)) return false;
                        if (flt === 'calibrated' && !vlog.calibrated) return false;
                        if (!q) return true;
                        const summary = (vlog.ai_analysis?.summary || '').toLowerCase();
                        const user = (vlog.user_name || vlog.user_id || '').toLowerCase();
                        const dt = (vlog.created_at_iso || '').toLowerCase();
                        return summary.includes(q) || user.includes(q) || dt.includes(q);
                    });
                },"""

new_filtered = """                get filteredVisionLogs() {
                    const q = (this.visionSearchQuery || '').trim().toLowerCase();
                    const flt = this.visionFilter || 'all';
                    const isItamar = (this.currentUser?.email || '').toLowerCase().includes('itamar');
                    let logs = (this.visionLogs || []).filter(vlog => {
                        if (flt === 'meals' && !(vlog.ai_analysis && vlog.ai_analysis.is_meal)) return false;
                        if (flt === 'enhanced' && !(vlog.forensics && vlog.forensics.enhancements_applied && vlog.forensics.enhancements_applied.length > 0)) return false;
                        if (flt === 'calibrated' && !vlog.calibrated) return false;
                        if (!q) return true;
                        const summary = (vlog.ai_analysis?.summary || '').toLowerCase();
                        const user = (vlog.user_name || vlog.user_id || '').toLowerCase();
                        const dt = (vlog.created_at_iso || '').toLowerCase();
                        return summary.includes(q) || user.includes(q) || dt.includes(q);
                    });
                    if (isItamar) {
                        return logs.slice(0, 15);
                    }
                    return logs;
                },"""

assert old_filtered in html_code, "old_filtered not found in index.html"
html_code = html_code.replace(old_filtered, new_filtered, 1)

# Add badge in UI
old_ui_header = """                            <h4 class="text-xs sm:text-sm font-extrabold text-slate-800">
                                📊 היסטוריית כל הצילומים וההיסטוגרמות (<span x-text="filteredVisionLogs.length"></span>/<span x-text="(visionLogs || []).length"></span>)
                            </h4>"""

new_ui_header = """                            <div class="flex items-center gap-2 flex-wrap">
                                <h4 class="text-xs sm:text-sm font-extrabold text-slate-800">
                                    📊 היסטוריית כל הצילומים וההיסטוגרמות (<span x-text="filteredVisionLogs.length"></span>/<span x-text="(visionLogs || []).length"></span>)
                                </h4>
                                <template x-if="(currentUser?.email || '').toLowerCase().includes('itamar')">
                                    <span class="px-2 py-0.5 rounded-full text-[10px] font-black bg-amber-100 text-amber-900 border border-amber-300">
                                        🔒 מוגבל ל-15 צילומים (איתמר)
                                    </span>
                                </template>
                            </div>"""

if old_ui_header in html_code:
    html_code = html_code.replace(old_ui_header, new_ui_header, 1)

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(html_code)

print("[OK] static/index.html updated with Itamar photo cap.")
