# -*- coding: utf-8 -*-
"""
test_messaging_hub.py
Verifies feature toggling, Telegram pairing, and unified chat message ingestion.
"""

import sys, os
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.getcwd())

import asyncio
from bot_server import (
    get_feature_flags,
    update_feature_flags,
    link_telegram_Account,
    unlink_telegram_Account,
    is_telegram_linked_for_user,
    process_chat_meal_message,
    _LOCAL_USERS,
)

def test_feature_flags():
    flags = get_feature_flags()
    assert "telegram_enabled" in flags
    assert "whatsapp_enabled" in flags
    print("[PASS] Initial feature flags:", flags)

    # Toggle
    updated = update_feature_flags(telegram_enabled=False, whatsapp_enabled=True)
    assert updated["telegram_enabled"] is False
    assert updated["whatsapp_enabled"] is True
    print("[PASS] Toggled feature flags successfully")

    # Reset
    update_feature_flags(telegram_enabled=True, whatsapp_enabled=False)
    print("[PASS] Reset feature flags to normal")

def test_account_linking():
    test_uid = "test-trainee-123"
    _LOCAL_USERS[test_uid] = {
        "id": test_uid,
        "name": "יוסי כהן",
        "email": "yossi@example.com",
        "link_code": "YOSSI77",
    }

    assert not is_telegram_linked_for_user(test_uid)

    # Link via deep-link code
    matched = link_telegram_Account(987654321, "YOSSI77")
    assert matched is not None
    assert matched["id"] == test_uid
    assert is_telegram_linked_for_user(test_uid)
    print("[PASS] Successfully linked Telegram user via link_code")

    # Unlink
    ok = unlink_telegram_Account(987654321, test_uid)
    assert ok
    assert not is_telegram_linked_for_user(test_uid)
    print("[PASS] Successfully unlinked Telegram user")

    # Relink via UID directly
    matched2 = link_telegram_Account(987654321, test_uid)
    assert matched2 is not None
    assert is_telegram_linked_for_user(test_uid)
    print("[PASS] Successfully linked Telegram user directly via UID")

async def test_unified_chat_engine():
    test_uid = "test-trainee-123"
    # Process simulated text meal
    res = await process_chat_meal_message(
        channel="telegram",
        user_id=test_uid,
        text="אכלתי לארוחת צהריים 200 גרם חזה עוף ואורז בסמטי",
    )
    assert res["success"] is True
    assert "reply_text" in res
    assert "ארוחת צהריים" in res["reply_text"]
    assert "חזה עוף" in res["reply_text"]
    print("[PASS] Unified chat message processed meal, resolved slot, and returned Hebrew summary!")
    print("[OUTPUT PREVIEW]:\n" + res["reply_text"])

if __name__ == '__main__':
    test_feature_flags()
    test_account_linking()
    asyncio.run(test_unified_chat_engine())
    print("\nALL BACKEND MESSAGING TESTS PASSED!")
