---
name: llmops-evals
description: Run automated golden evaluations, model routing tests, Red Teaming safety checks, and LLMOps telemetry for NutriAgent AI models, prompts, and food nutrition extractions.
---

# LLMOps, Evals & Red Teaming Skill for NutriAgent AI

Use this skill whenever modifying:
1. Gemini extraction prompts in `bot_server.py` (`analyze_meal`, `analyze_meal_image`, `analyze_meal_audio`, `generate_daily_coach_insight`).
2. Meal calibration rules, heuristic fallback logic, or Model Routing strategies (`fast_track` vs `deep_track`).
3. Red Teaming defenses (eating disorder protection, starvation guardrails, negative calorie defenses, prompt leak prevention).
4. Nutrient calculation or time classification algorithms.

## Workflow

1. **Before pushing prompt or model changes:**
   Always run the benchmark suite to verify accuracy and safety guardrails across all 14 golden & adversarial cases:
   ```powershell
   python evals_runner.py
   ```

2. **Verify Architecture & Dead-Clicks:**
   Ensure zero dead-clicks and 36/36 passing architectural suites:
   ```powershell
   python qa_guardian.py
   ```

3. **Check Routing & Safety Telemetry:**
   Verify endpoint `/api/admin/llmops/stats` for real-world user overrides, model routing distribution (fast vs deep), and blocked safety incidents.
