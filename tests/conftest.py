"""Shared pytest fixtures for libb-mail tests.

At import time, overrides mail.config values that the runtime code reads,
so all tests are deterministic regardless of the environment.
"""
from unittest.mock import MagicMock

import pytest

from libb import Setting
from mail import config as mail_config

Setting.unlock()
mail_config.mail.domain = 'example.com'
mail_config.mail.fromemail = 'jmilton@example.com'
mail_config.mail.provider = 'mandrill'
mail_config.mandrill.apikey = 'mockapi'
mail_config.ses.region = 'us-east-1'
mail_config.ses.access_key_id = None
mail_config.ses.secret_access_key = None
Setting.lock()


@pytest.fixture
def mock_boto3_client(monkeypatch):
    """Patch mail.client.boto3.client to return a MagicMock SES client.

    Returns the SES client mock whose calls (notably send_raw_email) can be
    inspected. Patches at the mail.client module path so the dotted access
    inside _send_via_ses is intercepted.
    """
    from mail import client as mail_client
    fake_ses = MagicMock()
    fake_ses.send_raw_email.return_value = {'MessageId': 'fake-ses-message-id'}
    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = fake_ses
    monkeypatch.setattr(mail_client, 'boto3', fake_boto3)
    return fake_ses


@pytest.fixture
def disable_boto3(monkeypatch):
    """Simulate boto3 not being installed (the soft-import set boto3 = None).
    """
    from mail import client as mail_client
    monkeypatch.setattr(mail_client, 'boto3', None)


@pytest.fixture
def mock_mandrill_client(monkeypatch):
    """Override the wrapt-patched Mandrill SDK with a controllable MagicMock.

    Returns the messages mock; tests set `.send.return_value` or `.send.side_effect`.
    Bypasses the wrapt patch in tests/__init__.py by replacing the SDK module
    reference inside mail.client.
    """
    from mail import client as mail_client
    fake_messages = MagicMock()
    fake_client = MagicMock()
    fake_client.messages = fake_messages
    fake_module = MagicMock()
    fake_module.Client = MagicMock(return_value=fake_client)
    monkeypatch.setattr(mail_client, 'MailchimpTransactional', fake_module)
    return fake_messages
