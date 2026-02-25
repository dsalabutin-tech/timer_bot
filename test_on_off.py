#!/usr/bin/env python3
"""Тесты для команд /on и /off — управление отправкой уведомлений."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Moscow")


class TestOnOff(unittest.TestCase):
    """Тесты ON/OFF флага уведомлений."""

    def setUp(self):
        import bot
        # Сбрасываем состояние перед каждым тестом
        bot._notifications_enabled = True
        bot._subscribers = {111, 222}
        bot._sent_notifications = set()

    def _make_update(self, text, user_id=1, username="admin"):
        update = MagicMock()
        update.effective_user.id = user_id
        update.effective_user.username = username
        update.effective_chat.id = user_id
        update.message.text = text
        update.message.reply_text = AsyncMock()
        return update

    def _make_context(self):
        context = MagicMock()
        context.bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))
        context.job_queue.run_once = MagicMock()
        return context

    @patch("bot.is_admin", return_value=True)
    def test_cmd_off_disables(self, _):
        """Команда /off выключает уведомления."""
        import bot
        update = self._make_update("/off")
        context = self._make_context()

        asyncio.run(bot.cmd_off(update, context))

        self.assertFalse(bot._notifications_enabled)
        update.message.reply_text.assert_called_once()
        self.assertIn("OFF", update.message.reply_text.call_args[0][0])

    @patch("bot.is_admin", return_value=True)
    def test_cmd_on_enables(self, _):
        """Команда /on включает уведомления."""
        import bot
        bot._notifications_enabled = False
        update = self._make_update("/on")
        context = self._make_context()

        asyncio.run(bot.cmd_on(update, context))

        self.assertTrue(bot._notifications_enabled)
        update.message.reply_text.assert_called_once()
        self.assertIn("ON", update.message.reply_text.call_args[0][0])

    @patch("bot.is_admin", return_value=False)
    def test_cmd_off_requires_admin(self, _):
        """Команда /off требует права админа."""
        import bot
        update = self._make_update("/off")
        context = self._make_context()

        asyncio.run(bot.cmd_off(update, context))

        # Флаг не изменился
        self.assertTrue(bot._notifications_enabled)
        update.message.reply_text.assert_called_once()
        self.assertIn("⛔", update.message.reply_text.call_args[0][0])

    @patch("bot.is_admin", return_value=False)
    def test_cmd_on_requires_admin(self, _):
        """Команда /on требует права админа."""
        import bot
        bot._notifications_enabled = False
        update = self._make_update("/on")
        context = self._make_context()

        asyncio.run(bot.cmd_on(update, context))

        self.assertFalse(bot._notifications_enabled)

    @patch("bot.get_notification_intervals", return_value=[15, 5, 1])
    @patch("bot.get_server_restart")
    @patch("bot.SessionLocal")
    def test_tick_off_no_messages_but_tracks(self, mock_session, mock_restart, mock_intervals):
        """При OFF: tick_notifications НЕ шлёт сообщения, но считает таймеры и делает auto_kill."""
        import bot
        bot._notifications_enabled = False

        now = datetime(2026, 2, 4, 12, 0, tzinfo=TZ)

        # Создаём мок босса с респом прямо сейчас
        mock_boss = MagicMock()
        mock_boss.id = 1
        mock_boss.name = "Тест"
        mock_boss.spawn_chance_percent = 100
        mock_boss.is_active = True
        mock_boss.last_kill_at = None
        mock_boss.first_spawn_minutes = 60
        mock_boss.respawn_minutes = 120

        db = MagicMock()
        mock_session.return_value = db
        mock_restart.return_value = datetime(2026, 2, 4, 9, 0, tzinfo=TZ)
        db.query.return_value.filter.return_value.all.return_value = [mock_boss]

        context = self._make_context()

        # Мокаем boss_next_spawn чтобы вернуть время "прямо сейчас"
        spawn_time = now - timedelta(seconds=30)
        with patch("bot.boss_next_spawn", return_value=spawn_time), \
             patch("bot.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            asyncio.run(bot.tick_notifications(context))

        # Сообщения НЕ отправлены
        context.bot.send_message.assert_not_called()

        # Но auto_kill запланирован (проверяем что run_once вызван с auto_kill_job)
        auto_kill_calls = [
            c for c in context.job_queue.run_once.call_args_list
            if c.kwargs.get("data", {}).get("boss_id") == 1
            or (len(c.args) > 1 and isinstance(c.kwargs.get("data"), dict) and c.kwargs["data"].get("boss_id") == 1)
        ]
        # run_once был вызван для auto_kill
        found_auto_kill = False
        for call in context.job_queue.run_once.call_args_list:
            args, kwargs = call
            if args and args[0] == bot.auto_kill_job:
                found_auto_kill = True
                break
        self.assertTrue(found_auto_kill, "auto_kill_job должен быть запланирован даже при OFF")

        # Уведомление записано в _sent_notifications (чтобы не дублировалось при включении ON)
        self.assertTrue(len(bot._sent_notifications) > 0, "_sent_notifications должен быть обновлён при OFF")

    @patch("bot.get_notification_intervals", return_value=[15, 5, 1])
    @patch("bot.get_server_restart")
    @patch("bot.SessionLocal")
    def test_tick_on_sends_messages(self, mock_session, mock_restart, mock_intervals):
        """При ON: tick_notifications отправляет сообщения как обычно."""
        import bot
        bot._notifications_enabled = True

        now = datetime(2026, 2, 4, 12, 0, tzinfo=TZ)

        mock_boss = MagicMock()
        mock_boss.id = 1
        mock_boss.name = "Тест"
        mock_boss.spawn_chance_percent = 100
        mock_boss.is_active = True

        db = MagicMock()
        mock_session.return_value = db
        mock_restart.return_value = datetime(2026, 2, 4, 9, 0, tzinfo=TZ)
        db.query.return_value.filter.return_value.all.return_value = [mock_boss]

        context = self._make_context()

        spawn_time = now - timedelta(seconds=30)
        with patch("bot.boss_next_spawn", return_value=spawn_time), \
             patch("bot.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            asyncio.run(bot.tick_notifications(context))

        # Сообщения отправлены (по 1 на каждого подписчика)
        self.assertEqual(context.bot.send_message.call_count, len(bot._subscribers))


if __name__ == "__main__":
    unittest.main()
