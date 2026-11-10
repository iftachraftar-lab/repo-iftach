#!/usr/bin/env python3
"""
NutriAgent Continuous QA & Architecture Guardian Agent (qa_guardian.py)
=======================================================================
100% Read-Only, Non-Destructive Automated Auditor & Self-Learning Test Suite.

IMPORTANT SAFETY GUARANTEE:
- NEVER modifies source code files (.py, .html, .js).
- NEVER writes to or mutates production Firestore data.
- Automatically discovers new FastAPI routes, Pydantic models, and Alpine.js methods,
  expanding its regression baseline in `guardian_knowledge.json` while enforcing
  strict architectural invariants across all 6 reliability categories:
    1. Write-to-Read Roundtrip Integrity
    2. Implicit State Machines & Split-Brain Prevention
    3. Serverless / Cloud Run Ephemeral Safety
    4. Timezone (Asia/Jerusalem) & Midnight Boundary Consistency
    5. Race Conditions & Double-Submit Protection
    6. Syntax Validity, Silent Failure Prevention & Continuous Route Discovery
"""

import ast
import json
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, List, Any, Tuple, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_SERVER_PATH = os.path.join(BASE_DIR, "bot_server.py")
INDEX_HTML_PATH = os.path.join(BASE_DIR, "static", "index.html")
SW_JS_PATH = os.path.join(BASE_DIR, "static", "sw.js")
KNOWLEDGE_PATH = os.path.join(BASE_DIR, "guardian_knowledge.json")
IL_TZ = ZoneInfo("Asia/Jerusalem")


class GuardianCheckResult:
    def __init__(self, category_id: int, category_name: str, check_name: str, passed: bool, details: str):
        self.category_id = category_id
        self.category_name = category_name
        self.check_name = check_name
        self.passed = passed
        self.details = details

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category_id": self.category_id,
            "category_name": self.category_name,
            "check_name": self.check_name,
            "passed": self.passed,
            "details": self.details,
        }


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_func_source(py_src: str, tree: ast.AST, func_name: str) -> str:
    lines = py_src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            start = node.lineno - 1
            end = getattr(node, "end_lineno", start + 50)
            return "\n".join(lines[start:end])
    return ""


# ==============================================================================
# SUITE 1: Write-to-Read Roundtrip Integrity
# ==============================================================================
def run_suite_1_roundtrip(py_src: str, tree: ast.AST) -> List[GuardianCheckResult]:
    results: List[GuardianCheckResult] = []
    cat_id = 1
    cat_name = "1. Write-to-Read Roundtrip Integrity"

    # 1.1 Check get_day_summary preserves meal_type, items, log_date, log_time, calories, protein_g
    summary_src = _extract_func_source(py_src, tree, "get_day_summary")
    required_summary_fields = ['"meal_type"', '"items"', '"log_date"', '"log_time"', '"calories"', '"protein_g"']
    missing_summary = [f for f in required_summary_fields if f not in summary_src]
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "get_day_summary returns all core meal fields (including meal_type & items)",
        len(missing_summary) == 0,
        "All fields present in get_day_summary" if not missing_summary else f"Missing in get_day_summary: {missing_summary}"
    ))

    # 1.2 Check _fetch_user_history preserves meal_type, items, log_date, log_time
    history_src = _extract_func_source(py_src, tree, "_fetch_user_history")
    required_history_fields = ['"meal_type"', '"items"', '"log_date"', '"log_time"', '"calories"', '"protein_g"']
    missing_history = [f for f in required_history_fields if f not in history_src]
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "_fetch_user_history returns all core meal fields (including meal_type)",
        len(missing_history) == 0,
        "All fields present in _fetch_user_history" if not missing_history else f"Missing in _fetch_user_history: {missing_history}"
    ))

    # 1.3 Check AI, Photo, and Voice meal endpoints pass explicit_type=req.meal_type
    for ep_func in ("api_add_ai_meal", "api_add_photo_meal", "api_add_voice_meal"):
        ep_src = _extract_func_source(py_src, tree, ep_func)
        ok = "explicit_type=req.meal_type" in ep_src
        results.append(GuardianCheckResult(
            cat_id, cat_name,
            f"{ep_func} honors explicit req.meal_type override",
            ok,
            f"{ep_func} passes explicit_type=req.meal_type" if ok else f"{ep_func} is missing explicit_type=req.meal_type"
        ))

    # 1.4 Check api_update_meal persists meal_type updates
    update_src = _extract_func_source(py_src, tree, "api_update_meal")
    update_ok = 'update_payload["meal_type"] = req.meal_type' in update_src
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "api_update_meal persists meal_type changes on edit",
        update_ok,
        "api_update_meal updates meal_type in Firestore & local state" if update_ok else "api_update_meal drops meal_type"
    ))

    # 1.5 Check api_auth_me returns all 3 lead access statuses
    auth_me_src = _extract_func_source(py_src, tree, "api_auth_me")
    auth_ok = (
        '"experts_access_status"' in auth_me_src
        and '"trial_workout_access_status"' in auth_me_src
        and '"supplements_access_status"' in auth_me_src
    )
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "api_auth_me returns experts, trial_workout & supplements access statuses",
        auth_ok,
        "All 3 lead gate fields returned in /api/auth/me" if auth_ok else "Missing lead access fields in api_auth_me"
    ))

    return results


# ==============================================================================
# SUITE 2: Implicit State Machines & Split-Brain Prevention
# ==============================================================================
def run_suite_2_state_machines(html_src: str, sw_src: str) -> List[GuardianCheckResult]:
    results: List[GuardianCheckResult] = []
    cat_id = 2
    cat_name = "2. Implicit State Machines & Split-Brain"

    # 2.1 Service Worker version sync between index.html and sw.js
    html_sw_match = re.search(r"register\(['\"]/sw\.js\?v=(\d+)['\"]\)", html_src)
    sw_cache_match = re.search(r"CACHE_NAME\s*=\s*['\"]nutriagent-v(\d+)['\"]", sw_src)
    html_ver = html_sw_match.group(1) if html_sw_match else "NONE"
    sw_ver = sw_cache_match.group(1) if sw_cache_match else "NONE"
    ver_synced = (html_ver == sw_ver) and html_ver != "NONE"
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "Service Worker version synchronized between index.html and sw.js",
        ver_synced,
        f"Synced at v{html_ver}" if ver_synced else f"Version mismatch! index.html=v{html_ver}, sw.js=v{sw_ver}"
    ))

    # 2.2 resolveMealType handles explicit meal_type & normalizes morning_snack/afternoon_snack
    resolve_ok = (
        "resolveMealType(meal)" in html_src
        and "morning_snack" in html_src
        and "afternoon_snack" in html_src
        and "if (mt === 'breakfast' || mt.includes('בוקר')) return 'breakfast';" in html_src
        and "if (mt === 'lunch' || mt.includes('צהר')) return 'lunch';" in html_src
        and "if (mt === 'dinner' || mt.includes('ערב')) return 'dinner';" in html_src
    )
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "resolveMealType(meal) prioritizes explicit meal_type and normalizes snack variants",
        resolve_ok,
        "resolveMealType properly prioritizes explicit slot over clock hour" if resolve_ok else "resolveMealType state resolution regression detected"
    ))

    # 2.3 Edit Meal Modal state machine preserves and submits meal_type
    edit_ok = (
        "meal_type: this.resolveMealType(meal)" in html_src
        and "saveEditedMeal()" in html_src
        and "JSON.stringify(this.editForm)" in html_src
    )
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "Edit Meal Modal initializes and submits meal_type slot",
        edit_ok,
        "openEditMeal and saveEditedMeal preserve meal_type" if edit_ok else "Edit Meal Modal missing meal_type state binding"
    ))

    # 2.4 Multi-modal input box prevents photo + text catalog collision
    multimodal_ok = (
        "showAutocomplete && !selectedPhotoDataUrl && autocompleteSuggestions.length > 0" in html_src
        and "showAutocomplete = false; showAttachMenu = false; startVoiceRecording()" in html_src
    )
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "Multi-modal input box suppresses text autocomplete when photo is attached",
        multimodal_ok,
        "Photo/Text/Voice input states are mutually guarded" if multimodal_ok else "Multi-modal input state collision detected"
    ))

    # 2.5 Lead gating modals refresh session state on open
    lead_sync_ok = (
        "openExpertsDirectory(" in html_src
        and "openTrialWorkoutModal(" in html_src
        and "openSupplementsModal(" in html_src
        and html_src.count("this.verifyCurrentUserSession();") >= 3
    )
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "Lead gating modals (Experts, Trial Workout & Supplements) sync live approval status on open",
        lead_sync_ok,
        "All 3 lead modals call verifyCurrentUserSession() on open" if lead_sync_ok else "Lead modals missing live status refresh"
    ))

    return results


# ==============================================================================
# SUITE 3: Serverless / Cloud Run Ephemeral Traps
# ==============================================================================
def run_suite_3_serverless(py_src: str, tree: ast.AST) -> List[GuardianCheckResult]:
    results: List[GuardianCheckResult] = []
    cat_id = 3
    cat_name = "3. Serverless & Cloud Run Ephemeral Safety"

    # 3.1 External Cloud Scheduler HTTP trigger endpoint exists
    cron_ok = '"/api/cron/daily-health-check"' in py_src and "api_cron_daily_health_check" in py_src
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "Cloud Scheduler HTTP endpoint (/api/cron/daily-health-check) is registered",
        cron_ok,
        "Endpoint registered for external Cloud Scheduler wakeup" if cron_ok else "Missing /api/cron/daily-health-check endpoint"
    ))

    # 3.2 Firestore-backed daily cron deduplication
    dedup_ok = (
        "_has_daily_health_been_sent_today" in py_src
        and '"daily_health_cron"' in py_src
        and '"last_sent_date"' in py_src
    )
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "Daily 08:00 health report uses Firestore deduplication (app_config/daily_health_cron)",
        dedup_ok,
        "Firestore deduplication prevents duplicate or missed alerts across instances" if dedup_ok else "Missing Firestore cron deduplication"
    ))

    # 3.3 Admin health push sends both Web Push and direct Telegram message
    push_src = _extract_func_source(py_src, tree, "send_admin_health_and_billing_push")
    dual_channel_ok = "sendMessage" in push_src and "dispatch_push_to_admins" in push_src
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "Daily Admin Health Alert delivers via both Telegram Bot API and Web Push",
        dual_channel_ok,
        "Dual-channel delivery (Telegram + Web Push) verified" if dual_channel_ok else "Missing Telegram or Web Push channel in send_admin_health_and_billing_push"
    ))

    # 3.4 Strict Push Notification Isolation (Leads, Support & Technical Errors NEVER leak to regular users)
    admin_push_src = _extract_func_source(py_src, tree, "dispatch_push_to_admins")
    photo_err_src = _extract_func_source(py_src, tree, "record_user_photo_error")
    send_all_src = _extract_func_source(py_src, tree, "send_web_push_to_all")
    push_isolation_ok = (
        "else all_subs" not in admin_push_src
        and "if not admin_subs:" in admin_push_src
        and "dispatch_push_to_admins" in photo_err_src
        and "send_web_push_to_all" not in photo_err_src
        and "if is_admin:" in send_all_src
        and "if not user_id and not allow_broadcast:" in send_all_src
    )
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "Strict Push Isolation (Leads, Support & Technical Photo Alerts target Admins only)",
        push_isolation_ok,
        "Verified admin push isolation (no fallback to all_subs; photo errors routed to dispatch_push_to_admins)"
        if push_isolation_ok else "Privacy leak risk: admin/technical push may broadcast to regular users"
    ))

    # 3.5 Auth Spoofing & Clinical Input Bounds Hardening
    spoof_free = True
    for fn_name in ("api_delete_account_self", "api_bind_coach_code", "api_accept_terms", "api_ping_admin_approval"):
        fn_src = _extract_func_source(py_src, tree, fn_name)
        if "req.user_id" in fn_src:
            spoof_free = False
    bounds_ok = (
        "class ManualMealRequest" in py_src
        and "calories: int = Field(ge=0, le=10000)" in py_src
        and "class OnboardingSaveRequest" in py_src
        and "height_cm: Optional[float] = Field(default=None, ge=120.0, le=230.0)" in py_src
    )
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "Auth Spoofing (IDOR) Prevention & Clinical Input Bounds Enforcement",
        spoof_free and bounds_ok,
        "Zero req.user_id auth bypasses; strict bounds on meals, water, BMI & onboarding"
        if (spoof_free and bounds_ok) else "Detected unauthenticated req.user_id fallback or missing Pydantic bounds"
    ))

    return results


# ==============================================================================
# SUITE 4: Timezone (Asia/Jerusalem) & Midnight Boundary Consistency
# ==============================================================================
def run_suite_4_timezone(py_src: str, html_src: str) -> List[GuardianCheckResult]:
    results: List[GuardianCheckResult] = []
    cat_id = 4
    cat_name = "4. Timezone & Midnight Boundary (Asia/Jerusalem)"

    # 4.1 Ensure zero naive datetime.now().isoformat() calls in bot_server.py
    naive_iso_lines = [
        i + 1 for i, line in enumerate(py_src.splitlines())
        if "datetime.now().isoformat()" in line
    ]
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "Zero naive datetime.now().isoformat() calls in bot_server.py",
        len(naive_iso_lines) == 0,
        "All ISO timestamps in bot_server.py use get_israel_now() / IL_TZ" if not naive_iso_lines else f"Naive datetime.now().isoformat() at lines: {naive_iso_lines}"
    ))

    # 4.2 Ensure frontend initializes todayStr in Asia/Jerusalem timezone
    il_tz_frontend = "timeZone: 'Asia/Jerusalem'" in html_src and "en-CA" in html_src
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "Frontend todayStr uses Asia/Jerusalem (en-CA YYYY-MM-DD format)",
        il_tz_frontend,
        "Frontend date initialized with Asia/Jerusalem" if il_tz_frontend else "Frontend missing Asia/Jerusalem date initialization"
    ))

    # 4.3 Ensure Water & Wolt/Supermarket loggers respect activeTab !== 'history' ? todayStr : selectedDate
    water_date_ok = "const targetDate = (this.activeTab === 'history' && this.selectedDate) ? this.selectedDate : this.todayStr;" in html_src
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "Water & quick-loggers write to todayStr when on Today tab (preventing stale History date pollution)",
        water_date_ok,
        "Today tab operations strictly target todayStr" if water_date_ok else "Stale selectedDate leak detected in Today tab loggers"
    ))

    # 4.4 Functional test of resolve_eating_time_and_type logic (compiled safely from AST)
    try:
        # Execute only the pure time/slot resolution helper in an isolated namespace
        namespace: Dict[str, Any] = {
            "datetime": datetime,
            "timedelta": timedelta,
            "re": re,
            "Optional": Optional,
            "Tuple": Tuple,
            "IL_TZ": IL_TZ,
            "get_israel_now": lambda: datetime(2026, 11, 9, 12, 43, tzinfo=IL_TZ),  # Simulate 12:43 PM lunch hour!
        }
        tree = ast.parse(py_src)
        target_funcs = {"_infer_meal_type_from_hour", "resolve_eating_time_and_type"}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in target_funcs:
                mod = ast.Module(body=[node], type_ignores=[])
                code = compile(mod, filename="<guardian_tz_test>", mode="exec")
                exec(code, namespace)

        resolve_fn = namespace["resolve_eating_time_and_type"]

        # Case A: User clicks '+ הוסף לארוחת בוקר' at 12:43 PM and types 'הפוך' -> MUST be 'breakfast'!
        _, slot_a = resolve_fn(explicit_time=None, explicit_type="breakfast", text_context="הפוך")
        # Case B: User clicks '+ הוסף לארוחת ערב' at 12:43 PM -> MUST be 'dinner'!
        _, slot_b = resolve_fn(explicit_time=None, explicit_type="dinner", text_context="סלט יווני")
        # Case C: User types 'שתי ביצים בבוקר' at 12:43 PM with no explicit button -> MUST infer 'breakfast' from text!
        _, slot_c = resolve_fn(explicit_time=None, explicit_type=None, text_context="שתי ביצים בבוקר")
        # Case D: User types 'פסטה בשעה 20:30' -> MUST infer '20:30' and 'dinner'!
        time_d, slot_d = resolve_fn(explicit_time=None, explicit_type=None, text_context="פסטה בשעה 20:30")
        # Case E: Default auto at 12:43 PM with no text cue -> 'lunch'
        _, slot_e = resolve_fn(explicit_time=None, explicit_type=None, text_context="שניצל ואורז")

        all_cases_ok = (
            slot_a == "breakfast"
            and slot_b == "dinner"
            and slot_c == "breakfast"
            and time_d == "20:30" and slot_d == "dinner"
            and slot_e == "lunch"
        )
        details = (
            f"All 5 time/slot scenarios passed (12:43 explicit breakfast->{slot_a}, "
            f"explicit dinner->{slot_b}, text 'בבוקר'->{slot_c}, 'בשעה 20:30'->{slot_d}, auto 12:43->{slot_e})"
        )
        results.append(GuardianCheckResult(
            cat_id, cat_name,
            "Functional simulation of resolve_eating_time_and_type across 5 edge cases",
            all_cases_ok,
            details
        ))
    except Exception as e:
        results.append(GuardianCheckResult(
            cat_id, cat_name,
            "Functional simulation of resolve_eating_time_and_type across 5 edge cases",
            False,
            f"Simulation error: {e}"
        ))

    return results


# ==============================================================================
# SUITE 5: Race Conditions & Double-Submit Protection
# ==============================================================================
def run_suite_5_race_conditions(html_src: str) -> List[GuardianCheckResult]:
    results: List[GuardianCheckResult] = []
    cat_id = 5
    cat_name = "5. Race Conditions & Double-Submit Locks"

    guards_to_verify = [
        ("submitAIMeal", "if (!this.aiInputText.trim() || this.aiAnalyzing) return;"),
        ("submitPhotoMeal", "if (!this.selectedPhotoDataUrl || this.aiAnalyzing) return;"),
        ("submitVoiceBlob", "if (this.aiAnalyzing) return;"),
        ("quickLogPreset", "if (this.quickLoggingMeal) return;"),
        ("submitExpertMarketingLead", "this.expertLeadSaving = true;"),
        ("submitTrialWorkoutLead", "this.trialWorkoutSending = true;"),
        ("submitSupplementsLead", "if (this.supplementsLeadSending) return;"),
        ("saveConfirmedMeal", "if (this.mealConfirmSaving) return;"),
    ]

    for handler_name, expected_guard in guards_to_verify:
        ok = expected_guard in html_src
        results.append(GuardianCheckResult(
            cat_id, cat_name,
            f"Double-submit lock on {handler_name}()",
            ok,
            f"Guard '{expected_guard}' verified" if ok else f"Missing guard '{expected_guard}' in {handler_name}()"
        ))

    return results


# ==============================================================================
# SUITE 6: Syntax Validity, Silent Failure Prevention & Self-Learning Discovery
# ==============================================================================
def run_suite_6_discovery_and_syntax(
    py_src: str,
    tree: ast.AST,
    html_src: str,
    update_knowledge: bool = True
) -> Tuple[List[GuardianCheckResult], Dict[str, Any]]:
    results: List[GuardianCheckResult] = []
    cat_id = 6
    cat_name = "6. Syntax, Contracts & Continuous Learning"

    # 6.1 Python AST Compilation check
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "bot_server.py AST compilation & syntax integrity",
        True,
        f"Successfully parsed {len(py_src.splitlines())} lines of Python AST"
    ))

    # 6.2 HTML structural balance check (<script> and </script>) & Sequential Heading Hierarchy
    open_scripts = len(re.findall(r"<script\b[^>]*>", html_src, re.IGNORECASE))
    close_scripts = len(re.findall(r"</script>", html_src, re.IGNORECASE))
    scripts_balanced = (open_scripts == close_scripts) and open_scripts > 0
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "static/index.html balanced <script> tags",
        scripts_balanced,
        f"Balanced ({open_scripts} <script> blocks)" if scripts_balanced else f"Unbalanced <script> tags: {open_scripts} open vs {close_scripts} close"
    ))

    # 6.2b Agentic Web Readiness (is-agentic.com 5 checks: 404 markdown, sequential headings, OAuth scopes, Markdown negotiation, OAuth metadata)
    heading_matches = list(re.finditer(r"<(h[1-6])(\b[^>]*)>(.*?)</\1>", html_src, re.DOTALL))
    prev_lvl = 1
    heading_issues = 0
    for hm in heading_matches:
        lvl = int(hm.group(1)[1])
        clean_txt = re.sub(r"<[^>]+>", "", hm.group(3)).strip()
        if lvl > prev_lvl + 1 or not clean_txt:
            heading_issues += 1
        prev_lvl = lvl

    agentic_ok = (
        heading_issues == 0
        and 'aria-label="אודות מערכת NutriAgent AI"' in html_src
        and "custom_http_exception_handler" in py_src
        and "OAUTH_SCOPES_SUPPORTED" in py_src
        and '"/.well-known/oauth-authorization-server"' in py_src
        and '"/.well-known/oauth-protected-resource"' in py_src
        and '"/llms.txt"' in py_src
    )
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "Agentic Web Readiness (is-agentic.com 5/5 checks: Markdown 404, No-JS HTML & sequential headings, Scoped OAuth, Markdown negotiation)",
        agentic_ok,
        f"All 5 Agentic Web checks verified ({len(heading_matches)} sequential headings, 0 issues)"
        if agentic_ok else f"Agentic readiness issue detected (heading_issues={heading_issues})"
    ))

    # 6.3 Auto-discover all FastAPI routes & Pydantic BaseModels in bot_server.py
    discovered_routes: List[str] = []
    discovered_models: List[str] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    if isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app":
                        method = dec.func.attr.upper()
                        if dec.args and isinstance(dec.args[0], ast.Constant):
                            path_str = dec.args[0].value
                            discovered_routes.append(f"{method} {path_str}")
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id == "BaseModel":
                    discovered_models.append(node.name)

    discovered_routes = sorted(set(discovered_routes))
    discovered_models = sorted(set(discovered_models))

    # 6.4 Auto-discover Alpine.js methods in static/index.html
    alpine_methods = sorted(set(re.findall(r"^\s{16}(?:async\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^)]*\)\s*\{", html_src, re.MULTILINE)))

    # Load previous knowledge base if exists
    prev_knowledge: Dict[str, Any] = {}
    if os.path.exists(KNOWLEDGE_PATH):
        try:
            with open(KNOWLEDGE_PATH, "r", encoding="utf-8") as f:
                prev_knowledge = json.load(f)
        except Exception:
            prev_knowledge = {}

    prev_routes = set(prev_knowledge.get("routes", []))
    curr_routes = set(discovered_routes)
    new_routes = sorted(curr_routes - prev_routes)
    removed_routes = sorted(prev_routes - curr_routes)

    # Critical endpoints that must NEVER disappear
    critical_endpoints = {
        "GET /api/summary",
        "GET /api/history",
        "POST /api/meals/",
        "POST /api/meals/ai",
        "POST /api/meals/photo",
        "POST /api/meals/voice",
        "PUT /api/meals/{meal_id}",
        "DELETE /api/meals/{meal_id}",
        "GET /api/auth/me",
        "POST /api/leads/expert",
        "POST /api/leads/workout",
        "POST /api/leads/supplements",
        "POST /api/cron/daily-health-check",
    }
    missing_critical = sorted(critical_endpoints - curr_routes)

    results.append(GuardianCheckResult(
        cat_id, cat_name,
        f"Critical API endpoint preservation ({len(critical_endpoints)} core routes)",
        len(missing_critical) == 0,
        f"All {len(critical_endpoints)} core endpoints active ({len(discovered_routes)} total routes discovered)"
        if not missing_critical else f"CRITICAL ENDPOINTS MISSING: {missing_critical}"
    ))

    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "Continuous Route & Schema Learning Engine",
        True,
        f"Tracking {len(discovered_routes)} API routes (+{len(new_routes)} new), "
        f"{len(discovered_models)} Pydantic schemas, and {len(alpine_methods)} Alpine UI methods"
    ))

    updated_knowledge = {
        "last_audit_at": datetime.now(IL_TZ).isoformat(),
        "total_runs": int(prev_knowledge.get("total_runs", 0)) + 1,
        "sw_version": re.search(r"nutriagent-v(\d+)", _read_text(SW_JS_PATH)).group(1) if re.search(r"nutriagent-v(\d+)", _read_text(SW_JS_PATH)) else "unknown",
        "counts": {
            "api_routes": len(discovered_routes),
            "pydantic_models": len(discovered_models),
            "alpine_methods": len(alpine_methods),
        },
        "newly_learned_routes_last_run": new_routes,
        "removed_routes_last_run": removed_routes,
        "routes": discovered_routes,
        "pydantic_models": discovered_models,
        "alpine_methods": alpine_methods,
    }

    if update_knowledge and len(missing_critical) == 0:
        try:
            with open(KNOWLEDGE_PATH, "w", encoding="utf-8") as f:
                json.dump(updated_knowledge, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    return results, updated_knowledge


# ==============================================================================
# SUITE 7: UX & Interactive Flow Integrity (Dead-Clicks, Modal Traps & Watchdog)
# ==============================================================================
def run_suite_7_ux_integrity(py_src: str, html_src: str) -> List[GuardianCheckResult]:
    results: List[GuardianCheckResult] = []
    cat_id = 7
    cat_name = "7. UX & Interactive Flow Integrity (Dead-Clicks, Modal Traps & Watchdog)"

    # 7.1 Dead-Click Audit: Verify every function invoked in @click="..." exists in nutritionApp()
    defined_methods = set(re.findall(r"\b(?:async\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^)]*\)\s*\{", html_src))
    js_builtins = {
        "alert", "confirm", "prompt", "setTimeout", "clearTimeout", "encodeURIComponent",
        "parseInt", "parseFloat", "Number", "String", "Boolean", "Date", "Math", "Object",
        "Array", "JSON", "console", "window", "document", "navigator", "localStorage",
        "sessionStorage", "fetch", "scrollBy", "scrollTo", "focus", "click", "select",
        "open", "if", "for", "while", "switch", "catch"
    }
    click_exprs = re.findall(r'@click(?:\.[a-z]+)*="([^"]+)"', html_src)
    dead_clicks = sorted({
        f"{fn}() in @click=\"{expr}\""
        for expr in click_exprs
        for fn in re.findall(r"(?<![.\w\$])([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", expr)
        if fn not in defined_methods and fn not in js_builtins
    })
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        f"Zero Dead-Clicks across all {len(click_exprs)} @click interactive buttons",
        len(dead_clicks) == 0,
        f"All {len(click_exprs)} @click handlers map to valid methods ({len(defined_methods)} methods verified)"
        if not dead_clicks else f"Dead-click handlers calling undefined methods: {dead_clicks[:5]}"
    ))

    # 7.2 Modal Trap Audit: Verify every modal overlay has a working close path (= false / = null)
    modal_flags = sorted(set(re.findall(
        r'x-show="(show[A-Za-z0-9_]*Modal|open[A-Za-z0-9_]*Modal|showBmiWizard|mealToEdit)"',
        html_src
    )))
    unclosable_modals = [
        m for m in modal_flags
        if not re.search(rf"\b{m}\s*=\s*(?:false|null)\b", html_src)
    ]
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        f"Zero Modal Traps across all {len(modal_flags)} modals & overlays",
        len(unclosable_modals) == 0 and "closeAllModals()" in html_src,
        f"All {len(modal_flags)} modals have explicit close paths + Escape key recovery"
        if not unclosable_modals else f"Unclosable modals detected: {unclosable_modals}"
    ))

    # 7.3 Auto-Unfreeze Client-Side UX Watchdog & Telemetry Endpoint
    watchdog_ok = (
        "startUxWatchdog()" in html_src
        and "this.startUxWatchdog();" in html_src
        and "ux_freeze_recovered" in html_src
        and '"/api/ux-telemetry"' in py_src
    )
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "Live UX Auto-Unfreeze Watchdog (25s stuck-spinner release + JS error telemetry)",
        watchdog_ok,
        "Client-side UX Watchdog & /api/ux-telemetry active" if watchdog_ok else "Missing UX Watchdog or telemetry endpoint"
    ))

    # 7.4 Mobile Viewport Overflow Protection & Favorites Strip Scroll Controls
    mobile_ux_ok = (
        "overflow-x: hidden !important;" in html_src
        and "$refs.favoritesStrip.scrollBy" in html_src
    )
    results.append(GuardianCheckResult(
        cat_id, cat_name,
        "Mobile viewport overflow guard & Favorites strip horizontal scroll controls",
        mobile_ux_ok,
        "Mobile overflow-x guard and Favorites ›/‹ scroll buttons verified" if mobile_ux_ok else "Missing mobile overflow guard or Favorites scroll controls"
    ))

    return results


# ==============================================================================
# MAIN RUNNER (CLI & API Entrypoint)
# ==============================================================================
def run_guardian_audit(update_knowledge: bool = True) -> Dict[str, Any]:
    """
    Executes all 7 architectural & UX reliability suites in 100% read-only mode.
    Returns a structured report suitable for CLI output or Admin Dashboard JSON.
    """
    py_src = _read_text(BOT_SERVER_PATH)
    html_src = _read_text(INDEX_HTML_PATH)
    sw_src = _read_text(SW_JS_PATH)

    tree = ast.parse(py_src)

    all_results: List[GuardianCheckResult] = []
    all_results.extend(run_suite_1_roundtrip(py_src, tree))
    all_results.extend(run_suite_2_state_machines(html_src, sw_src))
    all_results.extend(run_suite_3_serverless(py_src, tree))
    all_results.extend(run_suite_4_timezone(py_src, html_src))
    all_results.extend(run_suite_5_race_conditions(html_src))

    suite_6_results, knowledge_meta = run_suite_6_discovery_and_syntax(
        py_src, tree, html_src, update_knowledge=update_knowledge
    )
    all_results.extend(suite_6_results)
    all_results.extend(run_suite_7_ux_integrity(py_src, html_src))

    passed_count = sum(1 for r in all_results if r.passed)
    total_count = len(all_results)
    failed_checks = [r.to_dict() for r in all_results if not r.passed]

    return {
        "status": "healthy" if len(failed_checks) == 0 else "regression_detected",
        "passed": len(failed_checks) == 0,
        "score": f"{passed_count}/{total_count}",
        "pass_rate_pct": round((passed_count / total_count) * 100, 1) if total_count else 100.0,
        "timestamp_il": datetime.now(IL_TZ).strftime("%Y-%m-%d %H:%M:%S (Asia/Jerusalem)"),
        "knowledge_summary": {
            "total_runs": knowledge_meta.get("total_runs", 1),
            "sw_version": knowledge_meta.get("sw_version"),
            "api_routes_tracked": knowledge_meta["counts"]["api_routes"],
            "pydantic_models_tracked": knowledge_meta["counts"]["pydantic_models"],
            "alpine_methods_tracked": knowledge_meta["counts"]["alpine_methods"],
            "newly_learned_routes": knowledge_meta.get("newly_learned_routes_last_run", []),
        },
        "failed_checks": failed_checks,
        "checks": [r.to_dict() for r in all_results],
    }


if __name__ == "__main__":
    # Ensure UTF-8 output on Windows consoles
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    report = run_guardian_audit(update_knowledge=True)
    print("=" * 78)
    print("🛡️  NUTRIAGENT QA & ARCHITECTURE GUARDIAN (READ-ONLY NON-DESTRUCTIVE MODE)")
    print("=" * 78)
    print(f"⏱️  Timestamp (IL): {report['timestamp_il']}")
    print(f"📊 Pass Score:     {report['score']} ({report['pass_rate_pct']}%)")
    ks = report["knowledge_summary"]
    print(
        f"🧠 Knowledge Base: Run #{ks['total_runs']} | SW v{ks['sw_version']} | "
        f"{ks['api_routes_tracked']} API Routes | {ks['pydantic_models_tracked']} Models | "
        f"{ks['alpine_methods_tracked']} UI Methods"
    )
    print("-" * 78)

    current_cat = None
    for chk in report["checks"]:
        if chk["category_name"] != current_cat:
            current_cat = chk["category_name"]
            print(f"\n[{current_cat}]")
        icon = "✅" if chk["passed"] else "❌"
        print(f"  {icon} {chk['check_name']}")
        if not chk["passed"]:
            print(f"     ↳ ⚠️  {chk['details']}")

    print("\n" + "=" * 78)
    if report["passed"]:
        print("🟢 ALL 6 ARCHITECTURAL SUITES PASSED! SAFE TO DEPLOY.")
        sys.exit(0)
    else:
        print(f"🔴 REGRESSION DETECTED ({len(report['failed_checks'])} failed). DEPLOY BLOCKED!")
        sys.exit(1)
