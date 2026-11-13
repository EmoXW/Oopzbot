import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oopz.sdk_gateway import to_legacy
from oopz_sdk.models.area import AreaInfo, AreaUserDetail
from oopz_sdk.models.channel import ChannelSign, VoiceChannelMemberInfo
from oopz_sdk.models.event import AreaDisableEvent, FriendRequestEvent
from oopz_sdk.models.message import Message, MessageSendResult, PrivateSession
from oopz_sdk.models.person import FriendshipRequest, Profile
from oopz_sdk.services.area import AreaService


class MuteDeadlineTests(unittest.TestCase):
    def test_numeric_and_string_deadlines_normalize_for_application_consumers(self):
        for model in (AreaInfo, AreaUserDetail, ChannelSign):
            for value in (1794551386000, "1794551386000", 0, "0", None, "", "  "):
                with self.subTest(model=model.__name__, value=value):
                    detail = model.from_api({"disableTextTo": value, "disableVoiceTo": value})
                    expected = None if value is None or not str(value).strip() else int(value)
                    self.assertEqual(detail.disable_text_to, expected)
                    self.assertEqual(detail.disable_voice_to, expected)
                    if expected is not None:
                        legacy = to_legacy(detail)
                        self.assertIsInstance(legacy["disableTextTo"], int)
                        self.assertEqual(legacy["disableVoiceTo"], expected)

    def test_missing_deadlines_and_named_fields(self):
        detail = AreaUserDetail.from_api({})
        self.assertIsNone(detail.disable_text_to)
        self.assertIsNone(detail.disable_voice_to)
        detail = AreaUserDetail.model_validate({"disable_text_to": "1794551386000"})
        self.assertEqual(detail.disable_text_to, 1794551386000)

    def test_other_time_models_already_handle_numeric_values(self):
        stamp = 1794551386000
        for model, payload, field, expected in (
            (Message, {"timestamp": stamp, "topTime": stamp}, "timestamp", str(stamp)),
            (Message, {"topTime": stamp}, "top_time", str(stamp)),
            (MessageSendResult, {"messageId": "m", "timestamp": stamp}, "timestamp", str(stamp)),
            (PrivateSession, {"lastTime": stamp}, "last_time", str(stamp)),
            (VoiceChannelMemberInfo, {"enterTime": stamp}, "enter_time", str(stamp)),
            (FriendshipRequest, {"createTime": stamp}, "create_time", str(stamp)),
            (AreaDisableEvent, {"disableTo": stamp}, "disable_to", str(stamp)),
            (FriendRequestEvent, {"createTime": stamp}, "create_time", str(stamp)),
            (FriendRequestEvent, {"create_time": stamp}, "create_time", str(stamp)),
        ):
            with self.subTest(model=model.__name__, field=field):
                instance = model.model_validate(payload)
                self.assertEqual(getattr(instance, field), expected)

    def test_profile_expiry_fields_already_handle_string_and_empty_values(self):
        for value in (1794551386000, "1794551386000", "", None):
            with self.subTest(value=value):
                profile = Profile.from_api({"disabledEndTime": value, "personVIPEndTime": value})
                expected = int(value or 0)
                self.assertEqual(profile.disabled_end_time, expected)
                self.assertEqual(profile.person_vip_end_time, expected)


class MemberDetailResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_enter_channel_does_not_compare_strings_or_report_expired_mutes(self):
        from app.services.interaction.common_command_service import CommonCommandService

        for value, active in (("9999999999999", True), ("1", False), (None, False)):
            with self.subTest(value=value):
                result = to_legacy(ChannelSign.from_api({"disableTextTo": value, "disableVoiceTo": value}))
                sender = AsyncMock()
                runtime: Any = SimpleNamespace(sender=sender, music=SimpleNamespace(enter_voice_channel=AsyncMock(return_value=result)))
                with patch("app.services.interaction.common_command_service.get_resolver") as resolver:
                    resolver.return_value.channel.return_value = "示例频道"
                    await CommonCommandService(runtime).enter_channel("v", "c", "a")
                text = sender.send_message.await_args.args[0]
                self.assertEqual("文字禁言: 是" in text, active)
                self.assertEqual("语音禁言: 是" in text, active)

    async def test_role_command_does_not_report_expired_mutes(self):
        from app.services.community.role_service import RoleService

        for value, active in (("9999999999999", True), ("1", False), (None, False)):
            with self.subTest(value=value):
                sender = AsyncMock()
                sender.get_user_area_detail.return_value = to_legacy(AreaUserDetail.from_api({"disableTextTo": value, "disableVoiceTo": value}))
                runtime: Any = SimpleNamespace(sender=sender, services=SimpleNamespace(community=SimpleNamespace(target_resolution=SimpleNamespace(resolve_target=AsyncMock(return_value="u")))))
                with patch("app.services.community.role_service.get_resolver") as resolver:
                    resolver.return_value.user.return_value = "示例用户"
                    resolver.return_value.area.return_value = "示例域"
                    await RoleService(runtime).show_user_roles("u", "c", "a")
                text = sender.send_message.await_args.args[0]
                self.assertEqual("禁言至:" in text, active)
                self.assertEqual("禁麦至:" in text, active)

    async def test_sdk_service_parses_real_numeric_payload(self):
        service = AreaService.__new__(AreaService)
        payload = {"disableTextTo": 1794551386000, "disableVoiceTo": "1794551387000", "list": []}
        with patch.object(service, "_request_data", AsyncMock(return_value=payload)) as request:
            detail = await service.get_area_user_detail("area", "user")
        request.assert_awaited_once_with("GET", "/area/v3/userDetail", params={"area": "area", "target": "user"})
        self.assertEqual(to_legacy(detail)["disableTextTo"], 1794551386000)

    async def test_admin_member_detail_recognizes_active_and_expired_mutes(self):
        import web.admin.members._members as routes

        for deadline, active in ((9_999_999_999_999, True), (1, False), (0, False)):
            with self.subTest(deadline=deadline):
                detail = to_legacy(AreaUserDetail.from_api({"disableTextTo": deadline, "disableVoiceTo": str(deadline)}))
                sender = AsyncMock()
                sender.get_user_area_detail.return_value = detail
                sender.get_person_detail.return_value = {"name": "示例用户"}
                sender.get_assignable_roles.return_value = []
                with (
                    patch("web.admin.shared._runtime._get_sender", return_value=sender),
                    patch.object(routes, "_require_sender", return_value=sender),
                    patch.object(routes.MessageStatsDB, "get_user_ranking", AsyncMock(return_value=[])),
                ):
                    response = await routes.admin_member_detail("user", area="area")
                data = json.loads(bytes(response.body))
                self.assertTrue(data["ok"])
                self.assertEqual(data["muted"], active)
                self.assertEqual(data["mic_muted"], active)
                self.assertEqual(data["muted_until"], deadline if active else 0)
