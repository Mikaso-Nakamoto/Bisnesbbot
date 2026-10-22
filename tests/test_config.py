from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from bisnesbbot.config import load_settings


class SettingsTests(unittest.TestCase):
    @patch("bisnesbbot.config.load_dotenv")
    def test_vk_can_be_the_only_configured_channel(self, _load_dotenv) -> None:
        environment = {
            "BOT_TOKEN": "",
            "ADMIN_TELEGRAM_IDS": "",
            "VK_GROUP_TOKEN": "community-token",
            "VK_GROUP_ID": "12345",
            "VK_ADMIN_USER_IDS": "67890",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = load_settings()

        self.assertIsNone(settings.bot_token)
        self.assertEqual(settings.vk_group_token, "community-token")
        self.assertEqual(settings.vk_group_id, 12345)
        self.assertEqual(settings.admin_vk_user_ids, frozenset({67890}))
        self.assertIsNone(settings.max_bot_token)
        self.assertEqual(settings.max_admin_user_ids, frozenset())

    @patch("bisnesbbot.config.load_dotenv")
    def test_max_can_receive_admin_notifications(self, _load_dotenv) -> None:
        environment = {
            "BOT_TOKEN": "",
            "ADMIN_TELEGRAM_IDS": "",
            "VK_GROUP_TOKEN": "community-token",
            "VK_GROUP_ID": "12345",
            "VK_ADMIN_USER_IDS": "67890",
            "MAX_BOT_TOKEN": "max-token",
            "MAX_ADMIN_USER_IDS": "112233",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = load_settings()

        self.assertEqual(settings.max_bot_token, "max-token")
        self.assertEqual(settings.max_admin_user_ids, frozenset({112233}))

    @patch("bisnesbbot.config.load_dotenv")
    def test_max_admin_id_requires_max_token(self, _load_dotenv) -> None:
        environment = {
            "BOT_TOKEN": "",
            "ADMIN_TELEGRAM_IDS": "",
            "VK_GROUP_TOKEN": "community-token",
            "VK_GROUP_ID": "12345",
            "VK_ADMIN_USER_IDS": "",
            "MAX_BOT_TOKEN": "",
            "MAX_ADMIN_USER_IDS": "112233",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            self.assertRaisesRegex(ValueError, "MAX_ADMIN_USER_IDS"),
        ):
            load_settings()


if __name__ == "__main__":
    unittest.main()
