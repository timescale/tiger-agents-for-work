import importlib

import pytest

from tiger_agent.salesforce import constants


class TestSpamFilteringSalesforceOrigin:
    @pytest.fixture(autouse=True)
    def _restore_constants(self):
        yield
        importlib.reload(constants)

    def test_defaults_to_email_and_support_email(self, monkeypatch):
        monkeypatch.delenv("SPAM_FILTERING_SALESFORCE_ORIGIN", raising=False)
        importlib.reload(constants)
        expected = frozenset({"email", "support email"})
        assert expected == constants.SPAM_FILTERING_SALESFORCE_ORIGIN

    def test_reads_comma_delimited_env(self, monkeypatch):
        monkeypatch.setenv(
            "SPAM_FILTERING_SALESFORCE_ORIGIN", "Email,Support Email,Billing Email"
        )
        importlib.reload(constants)
        expected = frozenset({"email", "support email", "billing email"})
        assert expected == constants.SPAM_FILTERING_SALESFORCE_ORIGIN
