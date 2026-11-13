import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from oopz.sdk_gateway import AsyncOopzGateway


class AdminFriendsTest(unittest.TestCase):
    def setUp(self):
        import web.admin.friends as routes
        import web.web_player as player
        import web.web_rate_limit as limits

        self.routes, self.player = routes, player
        self.sender = SimpleNamespace(
            get_friendship=AsyncMock(return_value=[{"uid": "u1", "name": "张三", "online": True}, {"uid": "u2", "name": "李四", "online": False}]),
            get_friendship_requests=AsyncMock(return_value=[{"uid": "u3", "friendRequestId": 9007199254740993, "createTime": "1794551386000"}]),
            get_person_infos_batch=AsyncMock(return_value={"u3": {"name": "申请人"}}),
            post_friendship_response=AsyncMock(return_value={"status": True}),
            get_user_remark_name=AsyncMock(return_value="旧备注"),
            set_user_remark_name=AsyncMock(return_value={"status": True}),
        )
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(player, "_admin_enabled", return_value=True))
        stack.enter_context(patch.object(player, "_is_admin_authorized", return_value=True))
        stack.enter_context(patch("web.admin.shared._runtime._get_sender", return_value=self.sender))
        stack.enter_context(patch.object(routes, "_require_sender", return_value=self.sender))
        limits.reset_all()
        self.client = TestClient(player.app, client=("127.0.0.1", 50000))
        self.addCleanup(self.client.close)

    def test_all_friend_operations_require_admin_login(self):
        with patch.object(self.player, "_is_admin_authorized", return_value=False):
            for method, url in (("GET", "/admin/api/friends"), ("GET", "/admin/api/friends/requests"),
                                ("POST", "/admin/api/friends/requests/respond"),
                                ("GET", "/admin/api/friends/u1/remark"), ("POST", "/admin/api/friends/u1/remark")):
                with self.subTest(url=url, method=method):
                    self.assertEqual(self.client.request(method, url).status_code, 401)
        self.sender.post_friendship_response.assert_not_awaited()

    def test_page_inventory_search_and_pagination(self):
        page = self.client.get("/admin/friends")
        self.assertEqual(page.status_code, 200)
        self.assertIn("好友申请", page.text)
        self.assertIn("编辑好友备注", page.text)
        result = self.client.get("/admin/api/friends?offset=1&limit=1").json()
        self.assertTrue(result["ok"])
        self.assertEqual((result["total_count"], result["online_count"]), (2, 1))
        self.assertEqual(result["friends"][0]["uid"], "u2")
        result = self.client.get("/admin/api/friends", params={"search": "张"}).json()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["friends"][0]["name"], "张三")
        self.assertEqual(self.client.get("/admin/api/friends?limit=100").status_code, 422)

    def test_request_ids_are_strings_and_large_ids_keep_precision(self):
        result = self.client.get("/admin/api/friends/requests").json()
        self.assertEqual(result["requests"][0]["request_id"], "9007199254740993")
        self.assertEqual(result["requests"][0]["name"], "申请人")
        for agree in (True, False):
            response = self.client.post("/admin/api/friends/requests/respond", json={"uid": "u3", "request_id": "9007199254740993", "agree": agree})
            self.assertEqual(response.status_code, 200)
            self.sender.post_friendship_response.assert_awaited_with("u3", 9007199254740993, agree)

    def test_stale_or_wrong_user_request_cannot_be_processed(self):
        for uid, number in (("u1", "9007199254740993"), ("u3", "1")):
            response = self.client.post("/admin/api/friends/requests/respond", json={"uid": uid, "request_id": number, "agree": True})
            self.assertEqual(response.status_code, 409)
        self.sender.post_friendship_response.assert_not_awaited()

    def test_invalid_decision_and_cross_site_request_are_rejected(self):
        for payload in ({"uid": "u3", "request_id": "9007199254740993", "agree": "false"},
                        {"uid": "u3", "request_id": True, "agree": True},
                        {"uid": "u3", "request_id": 0, "agree": False}):
            self.assertEqual(self.client.post("/admin/api/friends/requests/respond", json=payload).status_code, 400)
        self.assertEqual(self.client.post("/admin/api/friends/requests/respond", json={}, headers={"origin": "https://other.example"}).status_code, 403)
        self.assertEqual(self.client.post("/admin/api/friends/requests/respond", content="{}").status_code, 415)
        self.sender.post_friendship_response.assert_not_awaited()

    def test_read_failures_do_not_appear_as_empty_lists(self):
        self.sender.get_friendship.side_effect = RuntimeError("upstream unavailable")
        self.assertEqual(self.client.get("/admin/api/friends").status_code, 502)
        self.sender.get_friendship_requests.side_effect = RuntimeError("upstream unavailable")
        self.assertEqual(self.client.get("/admin/api/friends/requests").status_code, 502)
        with patch("web.admin.shared._runtime._get_sender", return_value=None):
            self.assertEqual(self.client.get("/admin/api/friends").status_code, 503)

    def test_remark_read_write_clear_and_nonfriend_guard(self):
        self.assertEqual(self.client.get("/admin/api/friends/u1/remark").json()["remark"], "旧备注")
        for remark in ("新的备注", ""):
            self.assertTrue(self.client.post("/admin/api/friends/u1/remark", json={"remark": remark}).json()["ok"])
            self.sender.set_user_remark_name.assert_awaited_with("u1", remark)
        self.sender.set_user_remark_name.reset_mock()
        self.assertEqual(self.client.post("/admin/api/friends/stranger/remark", json={"remark": "test"}).status_code, 404)
        for remark in ("x" * 51, "a\nb", None):
            self.assertEqual(self.client.post("/admin/api/friends/u1/remark", json={"remark": remark}).status_code, 400)
        self.sender.set_user_remark_name.assert_not_awaited()

    def test_upstream_rejections_are_not_reported_as_success(self):
        self.sender.post_friendship_response.return_value = {"error": "failed"}
        self.assertEqual(self.client.post("/admin/api/friends/requests/respond", json={"uid": "u3", "request_id": "9007199254740993", "agree": False}).status_code, 502)
        self.sender.set_user_remark_name.return_value = {"status": False}
        self.assertEqual(self.client.post("/admin/api/friends/u1/remark", json={"remark": "test"}).status_code, 502)


class FriendshipGatewayTest(unittest.IsolatedAsyncioTestCase):
    async def test_strict_reads_preserve_errors_but_legacy_reads_stay_compatible(self):
        gateway = cast(Any, AsyncOopzGateway.__new__(AsyncOopzGateway))
        gateway.bot = SimpleNamespace(person=SimpleNamespace(get_friendship=AsyncMock(side_effect=RuntimeError("offline")), get_friendship_requests=AsyncMock(side_effect=RuntimeError("offline"))))
        self.assertEqual(await gateway.get_friendship(), [])
        self.assertEqual(await gateway.get_friendship_requests(), [])
        with self.assertRaises(RuntimeError):
            await gateway.get_friendship(strict=True)
        with self.assertRaises(RuntimeError):
            await gateway.get_friendship_requests(strict=True)
