# -*- coding: utf-8 -*-
"""
test_coach_platform.py
Verifies trainee creation by coach, invite link generation, Telegram pairing, and meal logging.
"""

import sys, os
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.getcwd())

import asyncio
from bot_server import (
    create_trainee_record,
    list_coach_trainees,
    find_trainee_by_invite_code,
    link_telegram_Account,
    process_chat_meal_message,
    get_day_summary,
)

async def test_full_coaching_flow():
    # 1. Coach Itamar creates a new trainee "דני כהן"
    trainee = create_trainee_record(
        coach_id="itamar",
        name="דני כהן",
        phone="050-1234567",
        target_calories=2000,
        target_protein=140.0,
        notes="מתאמן חדש, תוכנית חיטוב",
    )
    assert trainee is not None
    assert trainee["name"] == "דני כהן"
    inv_code = trainee["invite_code"]
    t_id = trainee["id"]
    print(f"[PASS] 1. Coach created trainee: {trainee['name']} (ID: {t_id})")
    print(f"       Invite Code: {inv_code}")
    print(f"       Telegram Link: {trainee['telegram_link']}")
    print(f"       WhatsApp text preview:\n       {trainee['whatsapp_share_text'][:100]}...")

    # 2. Verify find_trainee_by_invite_code
    found = find_trainee_by_invite_code(inv_code)
    assert found is not None
    assert found["id"] == t_id
    print("[PASS] 2. Trainee found by invite code successfully")

    # 3. Simulate Danny clicking the Telegram link -> runs /start pair_<inv_code>
    fake_tg_chat_id = 7788990011
    matched = link_telegram_Account(fake_tg_chat_id, inv_code)
    assert matched is not None
    assert matched["id"] == t_id
    assert matched["telegram_linked"] is True
    print("[PASS] 3. Danny linked his Telegram account via invite code!")

    # 4. Trainee sends a meal message in Telegram
    meal_res = await process_chat_meal_message(
        channel="telegram",
        user_id=t_id,
        text="אכלתי לארוחת בוקר שייק חלבון ו-2 תמרים",
    )
    assert meal_res["success"] is True
    print(f"[PASS] 4. Meal logged via Telegram under trainee {t_id}:")
    print(f"       {meal_res['reply_text']}")

    # 5. Coach Itamar views his Trainees Roster
    roster = list_coach_trainees(coach_id="itamar")
    assert len(roster) >= 1
    danny_in_roster = next((item for item in roster if item["id"] == t_id), None)
    assert danny_in_roster is not None
    assert danny_in_roster["telegram_linked"] is True
    assert danny_in_roster["today_calories"] > 0
    print("[PASS] 5. Coach Itamar sees Danny live in his roster with updated calories and linked status!")
    print(f"       Danny Today Calories: {danny_in_roster['today_calories']} / {danny_in_roster['target_calories']}")

if __name__ == '__main__':
    asyncio.run(test_full_coaching_flow())
    print("\nALL COACHING FLOW TESTS PASSED!")

