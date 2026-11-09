---
name: llmops-evals
description: Run automated golden evaluations and check LLMOps telemetry for NutriAgent AI models, prompts, and food nutrition extractions.
---

# LLMOps & Evals Skill for NutriAgent AI

Use this skill whenever modifying:
1. Gemini extraction prompts in `bot_server.py` (`analyze_meal`, `analyze_meal_image`, `analyze_meal_audio`, `generate_daily_coach_insight`).
2. Meal calibration rules or heuristic fallback logic.
3. Nutrient calculation or time classification algorithms.

## Workflow

1. **Before pushing prompt changes:**
   Always run the benchmark suite to verify accuracy has not degraded below 90%:
   ```powershell
   python evals_runner.py
   ```

2. **Verify Architecture & Dead-Clicks:**
   Ensure zero dead-clicks and 36/36 passing architectural suites:
   ```powershell
   python qa_guardian.py
   ```

3. **Check User Overrides & Telemetry:**
   Verify endpoint `/api/admin/llmops/stats` for real-world user overrides and latency regressions.
