from unittest.mock import MagicMock

import pytest

from tiger_agent.salesforce.constants import CLOUD_IMPACT_FIELD
from tiger_agent.salesforce.utils import create_case


@pytest.fixture
def salesforce_client():
    client = MagicMock()
    client.Case.create.return_value = {"success": True, "id": "500Nv00000ABCDE"}
    client.Case.get.return_value = {
        "Id": "500Nv00000ABCDE",
        "CaseNumber": "00012345",
        "Subject": "Cannot connect",
    }
    return client


class TestCreateCase:
    def test_sends_required_fields_to_salesforce(self, salesforce_client):
        create_case(
            salesforce_client=salesforce_client,
            subject="Cannot connect",
            description="Details",
            severity="Severity 3 - Medium",
            account_id="0011x00000ABCDE",
        )
        payload = salesforce_client.Case.create.call_args.args[0]
        assert payload == {
            "Subject": "Cannot connect",
            "Description": "Details",
            "Severity__c": "Severity 3 - Medium",
            "AccountId": "0011x00000ABCDE",
        }

    def test_includes_optional_fields_when_provided(self, salesforce_client):
        create_case(
            salesforce_client=salesforce_client,
            subject="Cannot connect",
            description="Details",
            severity="Severity 3 - Medium",
            account_id="0011x00000ABCDE",
            project_id="proj-a",
            service_id="svc-1",
            cloud_impact="High",
            origin="Slack",
        )
        payload = salesforce_client.Case.create.call_args.args[0]
        assert payload["Cloud_Project_ID__c"] == "proj-a"
        assert payload["Cloud_Service_ID__c"] == "svc-1"
        assert payload[CLOUD_IMPACT_FIELD] == "High"
        assert payload["Origin"] == "Slack"

    def test_omits_optional_fields_when_none(self, salesforce_client):
        create_case(
            salesforce_client=salesforce_client,
            subject="Cannot connect",
            description="Details",
            severity="Severity 3 - Medium",
            account_id="0011x00000ABCDE",
        )
        payload = salesforce_client.Case.create.call_args.args[0]
        assert "Cloud_Project_ID__c" not in payload
        assert "Cloud_Service_ID__c" not in payload
        assert CLOUD_IMPACT_FIELD not in payload
        assert "Origin" not in payload

    def test_omits_optional_fields_when_empty_string(self, salesforce_client):
        create_case(
            salesforce_client=salesforce_client,
            subject="Cannot connect",
            description="Details",
            severity="Severity 3 - Medium",
            account_id="0011x00000ABCDE",
            project_id="",
            service_id="",
            cloud_impact="",
            origin="",
        )
        payload = salesforce_client.Case.create.call_args.args[0]
        assert "Cloud_Project_ID__c" not in payload
        assert "Cloud_Service_ID__c" not in payload
        assert CLOUD_IMPACT_FIELD not in payload
        assert "Origin" not in payload

    def test_returns_case_data_hydrated_from_get(self, salesforce_client):
        case = create_case(
            salesforce_client=salesforce_client,
            subject="Cannot connect",
            description="Details",
            severity="Severity 3 - Medium",
            account_id="0011x00000ABCDE",
        )
        salesforce_client.Case.get.assert_called_once_with("500Nv00000ABCDE")
        assert case is not None
        assert case.Id == "500Nv00000ABCDE"
        assert case.CaseNumber == "00012345"
        assert case.Subject == "Cannot connect"

    def test_returns_none_when_create_reports_failure(self, salesforce_client):
        salesforce_client.Case.create.return_value = {"success": False, "id": None}
        result = create_case(
            salesforce_client=salesforce_client,
            subject="Cannot connect",
            description="Details",
            severity="Severity 3 - Medium",
            account_id="0011x00000ABCDE",
        )
        assert result is None
        salesforce_client.Case.get.assert_not_called()

    def test_returns_none_when_create_returns_no_id(self, salesforce_client):
        salesforce_client.Case.create.return_value = {"success": True, "id": None}
        result = create_case(
            salesforce_client=salesforce_client,
            subject="Cannot connect",
            description="Details",
            severity="Severity 3 - Medium",
            account_id="0011x00000ABCDE",
        )
        assert result is None
        salesforce_client.Case.get.assert_not_called()
