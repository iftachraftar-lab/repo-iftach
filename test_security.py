import sys
import unittest
from fastapi.testclient import TestClient

sys.stdout.reconfigure(encoding='utf-8')

import bot_server

client = TestClient(bot_server.app)

class TestSecurityHardening(unittest.TestCase):
    def setUp(self):
        # Reset local state
        bot_server._LOCAL_MEALS.clear()
        bot_server._LOCAL_WORKOUTS.clear()
        bot_server._LOCAL_FAVORITES.clear()
        bot_server._LOCAL_WEIGHTS.clear()
        bot_server._LOCAL_USERS.clear()
        
        # Setup two test users
        self.user_a = "u_userA_1111111111111111"
        self.user_b = "u_userB_2222222222222222"
        self.admin_user = "u_iftachraftar_425522a64076b0e9"
        
        bot_server._LOCAL_USERS[self.user_a] = {
            "id": self.user_a,
            "email": "userA@example.com",
            "name": "User A",
            "is_admin": False,
            "is_approved": True,
            "approved_by_owner": True,
            "is_blocked": False,
        }
        bot_server._LOCAL_USERS[self.user_b] = {
            "id": self.user_b,
            "email": "userB@example.com",
            "name": "User B",
            "is_admin": False,
            "is_approved": True,
            "approved_by_owner": True,
            "is_blocked": False,
        }
        bot_server._LOCAL_USERS[self.admin_user] = {
            "id": self.admin_user,
            "email": "iftachraftar@gmail.com",
            "name": "Admin",
            "is_admin": True,
            "is_approved": True,
            "approved_by_owner": True,
            "is_blocked": False,
        }

        self.token_a = bot_server.create_session_token(self.user_a)
        self.token_b = bot_server.create_session_token(self.user_b)
        self.token_admin = bot_server.create_session_token(self.admin_user)

    def test_session_token_cryptography(self):
        """Tokens must be verifiable and unforgeable."""
        self.assertEqual(bot_server.verify_session_token(self.token_a), self.user_a)
        self.assertIsNone(bot_server.verify_session_token(self.token_a + "tampered"))
        self.assertIsNone(bot_server.verify_session_token("invalid.token.structure"))

    def test_raw_x_user_id_spoofing_prevented(self):
        """Passing raw X-User-Id without valid cryptographic token must be ignored."""
        res = client.get("/api/auth/me", headers={"X-User-Id": self.admin_user})
        self.assertEqual(res.status_code, 401, "Raw X-User-Id header should not authenticate")

    def test_authenticated_me_route(self):
        """Valid session token authenticates successfully."""
        res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {self.token_a}"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["id"], self.user_a)
        self.assertFalse(data["is_admin"])

    def test_admin_route_authorization(self):
        """Regular user cannot access admin routes, but admin can."""
        res_user = client.get("/api/admin/users", headers={"Authorization": f"Bearer {self.token_a}"})
        self.assertEqual(res_user.status_code, 403)

        res_admin = client.get("/api/admin/users", headers={"Authorization": f"Bearer {self.token_admin}"})
        self.assertEqual(res_admin.status_code, 200)

    def test_meal_bola_protection(self):
        """User B must not be able to edit or delete User A's meal."""
        # Create meal for User A
        bot_server._LOCAL_MEALS.append({
            "id": "meal_123",
            "user_id": self.user_a,
            "description": "User A Meal",
            "calories": 500,
            "protein_g": 30.0,
            "log_date": "2026-10-26",
            "log_time": "12:00",
        })

        # User B attempts to edit User A's meal
        edit_payload = {
            "description": "Hacked Meal",
            "calories": 100,
            "protein_g": 10.0,
        }
        res_edit = client.put(
            "/api/meals/meal_123",
            json=edit_payload,
            headers={"Authorization": f"Bearer {self.token_b}"}
        )
        self.assertEqual(res_edit.status_code, 403, "User B should not be able to edit User A's meal")

        # User B attempts to delete User A's meal
        res_del = client.delete(
            "/api/meals/meal_123",
            headers={"Authorization": f"Bearer {self.token_b}"}
        )
        self.assertEqual(res_del.status_code, 403, "User B should not be able to delete User A's meal")

        # Verify meal is untouched
        self.assertEqual(len(bot_server._LOCAL_MEALS), 1)
        self.assertEqual(bot_server._LOCAL_MEALS[0]["description"], "User A Meal")

        # User A edits their own meal successfully
        res_owner_edit = client.put(
            "/api/meals/meal_123",
            json=edit_payload,
            headers={"Authorization": f"Bearer {self.token_a}"}
        )
        self.assertEqual(res_owner_edit.status_code, 200)
        self.assertEqual(bot_server._LOCAL_MEALS[0]["description"], "Hacked Meal")

    def test_workout_weight_favorite_bola(self):
        """BOLA protection on workouts, weights, favorites."""
        bot_server._LOCAL_WORKOUTS.append({"id": "w_1", "user_id": self.user_a})
        bot_server._LOCAL_FAVORITES.append({"id": "f_1", "user_id": self.user_a})
        bot_server._LOCAL_WEIGHTS.append({"id": "wt_1", "user_id": self.user_a})

        # User B cannot delete User A's records
        self.assertFalse(bot_server.delete_workout_db("w_1", user_id=self.user_b))
        self.assertFalse(bot_server.delete_favorite_db("f_1", user_id=self.user_b))
        self.assertFalse(bot_server.delete_weight_db("wt_1", user_id=self.user_b))

        self.assertEqual(len(bot_server._LOCAL_WORKOUTS), 1)
        self.assertEqual(len(bot_server._LOCAL_FAVORITES), 1)
        self.assertEqual(len(bot_server._LOCAL_WEIGHTS), 1)

        # User A can delete their own
        self.assertTrue(bot_server.delete_workout_db("w_1", user_id=self.user_a))
        self.assertTrue(bot_server.delete_favorite_db("f_1", user_id=self.user_a))
        self.assertTrue(bot_server.delete_weight_db("wt_1", user_id=self.user_a))

    def test_telegram_webhook_secret_verification(self):
        """Telegram webhook must reject requests without valid secret token."""
        secret = bot_server.get_telegram_webhook_secret()
        self.assertTrue(bool(secret))

        # Request with no secret -> 403
        res_no_sec = client.post("/webhook", json={"update_id": 1})
        self.assertEqual(res_no_sec.status_code, 403)

        # Request with wrong secret -> 403
        res_wrong_sec = client.post("/webhook", json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "bad"})
        self.assertEqual(res_wrong_sec.status_code, 403)

    def test_security_headers_present(self):
        """Security headers must be attached to responses."""
        res = client.get("/")
        self.assertEqual(res.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(res.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(res.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")

    def test_config_exposure_omits_primary_uid(self):
        """Public auth config must not expose internal user ID."""
        res = client.get("/api/auth/config")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("google_client_id", data)
        self.assertNotIn("primary_user_id", data)

    def test_barcode_lookup_endpoint(self):
        """Barcode lookup must require auth and return valid Israeli product information."""
        # Unauthenticated -> 401
        res_unauth = client.get("/api/barcode/7290004127329")
        self.assertEqual(res_unauth.status_code, 401)

        # Authenticated -> 200 with product details
        res = client.get("/api/barcode/7290004127329", headers={"Authorization": f"Bearer {self.token_a}"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("found"))
        self.assertEqual(data.get("barcode"), "7290004127329")
        self.assertIn("קוטג'", data.get("product_name", ""))
        self.assertEqual(data.get("calories_per_100g"), 96)

    def test_barcode_custom_save_endpoint(self):
        """Custom barcode saving and resolution must require auth and work seamlessly."""
        payload = {
            "barcode": "7298888888888",
            "product_name": "לחם כוסמין אורגני",
            "brand": "מאפיית הבוטיק",
            "calories_per_100g": 195,
            "protein_per_100g": 9.2,
            "carbs_per_100g": 38.0,
            "fat_per_100g": 1.2,
            "package_weight_g": 400
        }
        # Unauthenticated -> 401
        res_unauth = client.post("/api/barcode/custom", json=payload)
        self.assertEqual(res_unauth.status_code, 401)

        # Authenticated -> 200
        res_post = client.post("/api/barcode/custom", json=payload, headers={"Authorization": f"Bearer {self.token_a}"})
        self.assertEqual(res_post.status_code, 200)
        saved = res_post.json()
        self.assertTrue(saved.get("found"))
        self.assertEqual(saved.get("product_name"), "לחם כוסמין אורגני")

        # Now lookup should return it from cache
        res_get = client.get("/api/barcode/7298888888888", headers={"Authorization": f"Bearer {self.token_a}"})
        self.assertEqual(res_get.status_code, 200)
        retrieved = res_get.json()
        self.assertTrue(retrieved.get("found"))
        self.assertEqual(retrieved.get("protein_per_100g"), 9.2)

if __name__ == "__main__":
    unittest.main()

