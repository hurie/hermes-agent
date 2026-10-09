"""Tests for Mattermost channel_skill_bindings auto-skill resolution and config bridging."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from gateway.config import Platform, PlatformConfig
from gateway.config_loader import _bridged_keys
from gateway.platforms.base import resolve_channel_skills


def _make_adapter(extra=None):
    """Create a minimal MattermostAdapter stub with the given config.extra."""
    from plugins.platforms.mattermost.adapter import MattermostAdapter
    adapter = object.__new__(MattermostAdapter)
    adapter.config = MagicMock()
    adapter.config.extra = extra or {}
    return adapter


class TestMattermostConfigBridging:
    def test_channel_skill_bindings_bridged_to_mattermost_extra(self):
        platform_cfg = {
            "channel_skill_bindings": [
                {"id": "chan123", "skill": "intermittent-fasting"},
                {"id": "chan456", "skills": ["devops", "redmine"]},
            ],
        }
        bridged = _bridged_keys(Platform.MATTERMOST, platform_cfg, {})
        assert bridged.get("channel_skill_bindings") == [
            {"id": "chan123", "skill": "intermittent-fasting"},
            {"id": "chan456", "skills": ["devops", "redmine"]},
        ]


class TestMattermostResolveChannelSkills:
    def test_match_by_channel_id(self):
        adapter = _make_adapter({
            "channel_skill_bindings": [
                {"id": "chan_if", "skill": "intermittent-fasting"},
            ]
        })
        assert resolve_channel_skills(adapter.config.extra, "chan_if", None) == ["intermittent-fasting"]

    def test_match_by_channel_id_list_skills(self):
        adapter = _make_adapter({
            "channel_skill_bindings": [
                {"id": "chan_dev", "skills": ["devops", "redmine"]},
            ]
        })
        assert resolve_channel_skills(adapter.config.extra, "chan_dev", None) == ["devops", "redmine"]

    def test_thread_inherits_channel_parent(self):
        adapter = _make_adapter({
            "channel_skill_bindings": [
                {"id": "parent_chan", "skill": "parent-skill"},
            ]
        })
        # thread_id="post_root_999", parent_id="parent_chan"
        assert resolve_channel_skills(adapter.config.extra, "post_root_999", "parent_chan") == ["parent-skill"]

    @pytest.mark.parametrize("parent_first", [False, True])
    def test_thread_binding_wins_over_channel_parent(self, parent_first):
        bindings = [
            {"id": "thread_post_1", "skill": "thread-skill"},
            {"id": "parent_chan", "skill": "channel-skill"},
        ]
        adapter = _make_adapter({
            "channel_skill_bindings": bindings[::-1] if parent_first else bindings
        })
        assert resolve_channel_skills(adapter.config.extra, "thread_post_1", "parent_chan") == ["thread-skill"]

    def test_no_match_returns_none(self):
        adapter = _make_adapter({
            "channel_skill_bindings": [
                {"id": "chan_a", "skill": "skill-a"},
            ]
        })
        assert resolve_channel_skills(adapter.config.extra, "chan_unknown", None) is None


@pytest.mark.asyncio
class TestMattermostWsEventAutoSkill:
    async def test_handle_ws_event_injects_auto_skill(self):
        from plugins.platforms.mattermost.adapter import MattermostAdapter
        adapter = object.__new__(MattermostAdapter)
        adapter._bot_user_id = "bot_id_123"
        adapter._dedup = MagicMock()
        adapter._dedup.is_duplicate.return_value = False
        adapter._should_thread = MagicMock(return_value=False)
        adapter._download_attachments = AsyncMock(return_value=([], []))
        adapter.build_source = MagicMock(return_value=MagicMock())
        adapter.handle_message = AsyncMock()

        adapter.config = MagicMock()
        adapter.config.extra = {
            "channel_skill_bindings": [
                {"id": "chan_if", "skill": "intermittent-fasting"},
            ]
        }

        event = {
            "event": "posted",
            "data": {
                "channel_type": "O",
                "post": json.dumps({
                    "id": "post_1",
                    "user_id": "user_456",
                    "channel_id": "chan_if",
                    "message": "Halo Hermes",
                    "root_id": "",
                }),
            },
        }

        # Mock gating to allow message
        adapter._apply_channel_gating = MagicMock(return_value="Halo Hermes")

        await adapter._handle_ws_event(event)

        assert adapter.handle_message.called
        msg_event = adapter.handle_message.call_args[0][0]
        assert msg_event.auto_skill == ["intermittent-fasting"]
