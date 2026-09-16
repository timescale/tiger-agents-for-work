from unittest.mock import MagicMock

import pytest

from tiger_agent.salesforce.constants import CLOUD_IMPACT_FIELD, SEVERITY_FIELD
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

    def test_omits_severity_when_none(self, salesforce_client):
        create_case(
            salesforce_client=salesforce_client,
            subject="Cannot connect",
            description="Details",
            severity=None,
            account_id="0011x00000ABCDE",
        )
        payload = salesforce_client.Case.create.call_args.args[0]
        assert SEVERITY_FIELD not in payload

    def test_omits_severity_when_empty_string(self, salesforce_client):
        create_case(
            salesforce_client=salesforce_client,
            subject="Cannot connect",
            description="Details",
            severity="",
            account_id="0011x00000ABCDE",
        )
        payload = salesforce_client.Case.create.call_args.args[0]
        assert SEVERITY_FIELD not in payload

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


class TestBuildCaseQuery:
    def test_no_filters_selects_detail_fields_ordered_by_close_date(self):
        from tiger_agent.salesforce.constants import CASE_DETAIL_FIELDS
        from tiger_agent.salesforce.utils import build_case_query

        soql = build_case_query()
        assert soql.startswith(f"SELECT {', '.join(CASE_DETAIL_FIELDS)} FROM Case")
        assert " WHERE " not in soql
        assert soql.endswith("ORDER BY ClosedDate DESC NULLS LAST, CreatedDate DESC")

    def test_each_filter_renders_its_clause(self):
        from tiger_agent.salesforce.utils import build_case_query

        soql = build_case_query(
            case_ids=["500A", "500B"],
            case_numbers=["00012345"],
            case_type="How to - Consultative",
            status="Closed",
            closed_after="2026-06-01T00:00:00Z",
            closed_before="2026-07-01T00:00:00Z",
            limit=25,
        )
        where = soql.split(" WHERE ", 1)[1].split(" ORDER BY ", 1)[0]
        assert where.split(" AND ") == [
            "Id IN ('500A', '500B')",
            "CaseNumber IN ('00012345')",
            "Type = 'How to - Consultative'",
            "Status = 'Closed'",
            "ClosedDate >= 2026-06-01T00:00:00Z",
            "ClosedDate < 2026-07-01T00:00:00Z",
        ]
        assert soql.endswith(" LIMIT 25")

    def test_zero_limit_means_no_limit(self):
        from tiger_agent.salesforce.utils import build_case_query

        assert " LIMIT " not in build_case_query(status="Closed", limit=0)

    def test_string_values_are_quoted_and_escaped(self):
        from tiger_agent.salesforce.utils import build_case_query

        soql = build_case_query(case_type="O'Reilly \\ Co")
        assert "Type = 'O\\'Reilly \\\\ Co'" in soql

    def test_custom_fields_are_used(self):
        from tiger_agent.salesforce.utils import build_case_query

        soql = build_case_query(fields=["Id", "Subject"])
        assert soql.startswith("SELECT Id, Subject FROM Case")


class TestGetCases:
    def test_uses_query_all_and_returns_case_data(self):
        from tiger_agent.salesforce.utils import get_cases

        client = MagicMock()
        client.query_all.return_value = {
            "records": [
                {
                    "Id": "500A",
                    "CaseNumber": "00000001",
                    "Type": "How to - Consultative",
                    "Final_Resolution__c": "Use add_retention_policy.",
                }
            ]
        }
        cases = get_cases(client, case_type="How to - Consultative", status="Closed")
        soql = client.query_all.call_args.args[0]
        assert "Type = 'How to - Consultative'" in soql
        assert "Status = 'Closed'" in soql
        assert len(cases) == 1
        assert cases[0].Id == "500A"
        assert cases[0].Type == "How to - Consultative"
        assert cases[0].Final_Resolution__c == "Use add_retention_policy."

    def test_returns_empty_list_on_error(self):
        from tiger_agent.salesforce.utils import get_cases

        client = MagicMock()
        client.query_all.side_effect = RuntimeError("boom")
        assert get_cases(client, case_ids=["500A"]) == []


class TestGetCaseThread:
    def test_populates_direction_and_sender_fields(self):
        from tiger_agent.salesforce.utils import get_case_thread

        client = MagicMock()
        client.query.return_value = {
            "records": [
                {
                    "Id": "02sA",
                    "ParentId": "500A",
                    "Incoming": True,
                    "FromAddress": "customer@example.com",
                    "FromName": "Customer",
                    "TextBody": "How do I set retention?",
                    "Subject": "Retention",
                    "MessageDate": "2026-06-01T12:00:00.000+0000",
                },
                {
                    "Id": "02sB",
                    "ParentId": "500A",
                    "Incoming": False,
                    "FromAddress": "support@tigerdata.com",
                    "FromName": "Support",
                    "TextBody": None,
                    "HtmlBody": "<p>Use add_retention_policy.</p>",
                    "Subject": "Re: Retention",
                    "MessageDate": "2026-06-01T14:00:00.000+0000",
                },
            ]
        }
        thread = get_case_thread(client, "500A")
        soql = client.query.call_args.args[0]
        assert "Parent.Id = '500A'" in soql
        assert "ORDER BY MessageDate ASC" in soql

        customer, support = thread
        assert customer.Incoming is True
        assert customer.FromAddress == "customer@example.com"
        assert customer.TextBody == "How do I set retention?"
        assert customer.Body == "How do I set retention?"
        assert support.Incoming is False
        assert support.FromName == "Support"
        assert support.TextBody is None
        assert support.HtmlBody == "<p>Use add_retention_policy.</p>"
        # legacy fallback: Body falls back to Subject when TextBody is empty
        assert support.Body == "Re: Retention"
        assert support.CreatedBy.Email == "support@tigerdata.com"
