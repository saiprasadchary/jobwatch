from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import notifier


class FakeSMTP:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.logged_in_as: tuple[str, str] | None = None
        self.sent_messages = []

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def starttls(self) -> None:
        return None

    def login(self, username: str, password: str) -> None:
        self.logged_in_as = (username, password)

    def send_message(self, message) -> None:
        self.sent_messages.append(message)


class FakeIMAP:
    def __init__(self, _host: str) -> None:
        self.selected_folder = ""
        self.inbox_has_message = False
        self.search_history: list[tuple[str, tuple[str, ...]]] = []
        self.store_history: list[tuple[bytes, str, str]] = []
        self.copy_history: list[tuple[str, str]] = []
        self.append_history: list[tuple[str, bytes]] = []

    def login(self, _username: str, _password: str) -> tuple[str, list[bytes]]:
        return "OK", [b"Logged in"]

    def logout(self) -> tuple[str, list[bytes]]:
        return "BYE", [b"Logged out"]

    def select(self, folder: str) -> tuple[str, list[bytes]]:
        self.selected_folder = folder.strip('"')
        return "OK", [b"1"]

    def search(self, _charset, *criteria: str) -> tuple[str, list[bytes]]:
        self.search_history.append((self.selected_folder, criteria))
        if self.selected_folder == "INBOX" and self.inbox_has_message:
            return "OK", [b"42"]
        if self.selected_folder in ("[Gmail]/All Mail", "[Google Mail]/All Mail", "All Mail"):
            return "OK", [b"41"]
        return "OK", [b""]

    def store(self, message_id: bytes, command: str, flags: str) -> tuple[str, list[bytes]]:
        self.store_history.append((message_id, command, flags))
        return "OK", [b"stored"]

    def copy(self, message_set: str, folder: str) -> tuple[str, list[bytes]]:
        self.copy_history.append((message_set, folder))
        self.inbox_has_message = True
        return "OK", [b"copied"]

    def append(self, folder: str, _flags, _date_time, message: bytes) -> tuple[str, list[bytes]]:
        self.append_history.append((folder, message))
        self.inbox_has_message = True
        return "OK", [b"appended"]


class NotifierDeliveryTests(unittest.TestCase):
    def test_send_email_promotes_same_gmail_alert_back_to_unread_inbox(self) -> None:
        jobs = [
            {
                "job_id": "job-1",
                "company": "Example",
                "title": "Software Development Engineer",
                "location": "Austin, TX",
                "url": "https://example.com/jobs/1",
                "posted_at": "2026-05-19T12:00:00Z",
                "source": "greenhouse",
                "rank_band": "Top",
                "rank_score": 140,
                "rank_reasons": ["posted in the last hour", 'matched "Software Development Engineer"'],
                "rank_reason_text": 'posted in the last hour; matched "Software Development Engineer"',
            }
        ]
        config = {
            "notification": {
                "email": "me@gmail.com",
                "smtp_host": "smtp.gmail.com",
                "smtp_port": 587,
                "subject_prefix": "[JobWatch Inbox]",
            },
            "ranking": {"top_picks": 3, "preferred_companies": []},
        }

        smtp_instances: list[FakeSMTP] = []
        imap_instances: list[FakeIMAP] = []

        def fake_smtp(host: str, port: int) -> FakeSMTP:
            instance = FakeSMTP(host, port)
            smtp_instances.append(instance)
            return instance

        def fake_imap(host: str) -> FakeIMAP:
            instance = FakeIMAP(host)
            imap_instances.append(instance)
            return instance

        with patch.dict(
            os.environ,
            {"JOBWATCH_EMAIL_USER": "me@gmail.com", "JOBWATCH_EMAIL_PASSWORD": "app-password"},
            clear=False,
        ), patch.object(notifier.smtplib, "SMTP", side_effect=fake_smtp), patch.object(
            notifier.imaplib, "IMAP4_SSL", side_effect=fake_imap
        ), patch.object(
            notifier, "is_h1b_sponsor", return_value=False
        ):
            sent, detail = notifier.send_email(jobs, config)

        self.assertTrue(sent)
        self.assertIn("Gmail Inbox", detail)
        self.assertEqual(len(smtp_instances), 1)
        self.assertEqual(len(smtp_instances[0].sent_messages), 1)
        self.assertEqual(len(imap_instances), 1)
        self.assertTrue(imap_instances[0].copy_history)
        self.assertTrue(imap_instances[0].store_history)
        self.assertFalse(imap_instances[0].append_history)

    def test_send_email_skips_gmail_promotion_for_different_recipient(self) -> None:
        jobs = [
            {
                "job_id": "job-1",
                "company": "Example",
                "title": "Software Development Engineer",
                "location": "Austin, TX",
                "url": "https://example.com/jobs/1",
                "posted_at": "2026-05-19T12:00:00Z",
                "source": "greenhouse",
                "rank_band": "Top",
                "rank_score": 140,
                "rank_reasons": ["posted in the last hour"],
                "rank_reason_text": "posted in the last hour",
            }
        ]
        config = {
            "notification": {
                "email": "alerts@example.com",
                "smtp_host": "smtp.gmail.com",
                "smtp_port": 587,
            },
            "ranking": {"top_picks": 3, "preferred_companies": []},
        }

        smtp_instances: list[FakeSMTP] = []

        def fake_smtp(host: str, port: int) -> FakeSMTP:
            instance = FakeSMTP(host, port)
            smtp_instances.append(instance)
            return instance

        with patch.dict(
            os.environ,
            {"JOBWATCH_EMAIL_USER": "me@gmail.com", "JOBWATCH_EMAIL_PASSWORD": "app-password"},
            clear=False,
        ), patch.object(notifier.smtplib, "SMTP", side_effect=fake_smtp), patch.object(
            notifier, "is_h1b_sponsor", return_value=False
        ):
            sent, detail = notifier.send_email(jobs, config)

        self.assertTrue(sent)
        self.assertEqual(detail, "Email sent to alerts@example.com")
        self.assertEqual(len(smtp_instances), 1)
        self.assertEqual(len(smtp_instances[0].sent_messages), 1)


if __name__ == "__main__":
    unittest.main()
