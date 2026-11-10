import asyncio
import base64
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from plugins.delta_force_v2 import DeltaForceV2Plugin  # noqa: E402
from plugins.delta_force_v2.api import ApiError, DeltaApi, login_state  # noqa: E402
from plugins.delta_force_v2.formatters import render  # noqa: E402
from plugins.delta_force_v2.store import SessionStore  # noqa: E402


class ProtocolTests(unittest.TestCase):
    def test_nested_login_status(self):
        self.assertEqual(login_state({"status": "pending", "code": 1}), "pending")
        self.assertEqual(login_state({"status": "scanned", "code": 0}), "pending")
        self.assertEqual(login_state({"status": "expired", "code": 0}), "expired")
        self.assertEqual(login_state({"code": 0, "frameworkToken": "private"}), "success")
        self.assertEqual(login_state({}), "pending")

    def test_secret_fields_not_rendered(self):
        output = render("信息", {"frameworkToken": "private", "cookie": "private", "nickname": "玩家"})
        self.assertNotIn("private", output)
        self.assertIn("玩家", output)

    def test_command_prefix_and_safe_defaults(self):
        plugin: Any = DeltaForceV2Plugin()
        self.assertFalse(plugin.config_spec.to_example()["enabled"])
        self.assertEqual(plugin.command_capabilities.mention_prefixes, ("三角洲",))


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_headers_and_token_transport(self):
        api = DeltaApi({"base_url": "https://example.com", "api_key": "key", "client_id": "client"})
        response = SimpleNamespace(status=200, json=AsyncMock(return_value={"code": 0, "data": {"list": []}}))
        context = AsyncMock()
        context.__aenter__.return_value = response
        session = SimpleNamespace(request=Mock(return_value=context))
        api.http.session = AsyncMock(return_value=session)
        result = await api.request("/api/v1/df/person/record", token="game-token", params={"page": "1"})
        self.assertEqual(result, {"list": []})
        kwargs = session.request.call_args.kwargs
        self.assertEqual(kwargs["headers"]["X-Framework-Token"], "game-token")
        self.assertEqual(kwargs["headers"]["X-API-Key"], "key")
        self.assertNotIn("token", kwargs["params"])
        self.assertFalse(kwargs["allow_redirects"])

    async def test_binding_failure_does_not_save_session(self):
        plugin: Any = DeltaForceV2Plugin()
        plugin.config = {"client_id": "c", "login_page_base_url": "http://localhost:10000"}
        plugin.api = SimpleNamespace(request=AsyncMock(side_effect=[
            {"frameworkToken": "private", "qr_image": base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()},
            {"code": 0, "frameworkToken": "final"},
            ApiError("绑定失败"),
        ]))
        plugin.store = SimpleNamespace(put=AsyncMock())
        sender = SimpleNamespace(
            upload_and_send_private_image=AsyncMock(return_value={"id": "message"}),
            send_private_message=AsyncMock(return_value={"status": True}),
        )
        with patch("plugins.delta_force_v2.asyncio.sleep", new_callable=AsyncMock):
            await plugin._login("user", "qq", SimpleNamespace(sender=sender))
        plugin.store.put.assert_not_called()
        self.assertEqual(plugin.api.request.call_args.kwargs["body"]["user_identifier"], "user")

    async def test_persistence_and_user_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "store.db"
            await SessionStore(path).put("c", "a", "private")
            store = SessionStore(path)
            self.assertEqual(await store.get("c", "a"), "private")
            self.assertEqual(await store.get("other", "a"), "")
            self.assertEqual(await store.get("c", "b"), "")
            await store.delete("c", "a")
            self.assertEqual(await store.get("c", "a"), "")

    async def test_private_failure_never_binds_or_sends_public_qr(self):
        plugin: Any = DeltaForceV2Plugin()
        plugin.config = {"client_id": "c", "login_page_base_url": "http://localhost:10000"}
        plugin.api = SimpleNamespace(request=AsyncMock(return_value={
            "frameworkToken": "private", "qr_image": base64.b64encode(b"\x89PNG\r\n\x1a\n").decode(),
        }))
        sender = SimpleNamespace(
            upload_and_send_private_image=AsyncMock(return_value={"error": "denied"}),
            send_private_message=AsyncMock(return_value={"error": "denied"}), send_message=AsyncMock(),
        )
        await plugin._login("user", "qq", SimpleNamespace(sender=sender))
        self.assertEqual(plugin.api.request.await_count, 1)
        sender.send_message.assert_not_called()
        sender.upload_and_send_private_image.assert_not_called()

    async def test_unload_cancels_tasks(self):
        plugin: Any = DeltaForceV2Plugin()
        task = asyncio.create_task(asyncio.sleep(100))
        plugin.tasks["user"] = task
        api = SimpleNamespace(close=AsyncMock())
        plugin.api = api
        await plugin.on_unload()
        self.assertTrue(task.cancelled())
        api.close.assert_awaited_once()

    async def test_network_errors_are_safe_and_post_is_not_retried(self):
        api = DeltaApi({"base_url": "https://example.com", "api_key": "private", "client_id": "c", "request_retries": 3})
        api.http.session = AsyncMock(side_effect=TimeoutError("private"))
        with self.assertRaises(ApiError) as error:
            await api.request("/api/v1/user/bindings", method="POST")
        self.assertNotIn("private", str(error.exception))
        self.assertEqual(api.http.session.await_count, 1)
        await api.close()


if __name__ == "__main__":
    unittest.main()
