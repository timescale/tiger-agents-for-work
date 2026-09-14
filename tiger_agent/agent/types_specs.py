from tiger_agent.agent.types import DroppedStep, InvestigationReport


def _report(**overrides) -> InvestigationReport:
    base = dict(
        answer="The service is paused for billing.",
        evidence=["deployer state Paused", "two unpaid invoices"],
        confidence="high",
        confidence_reason="both sources agree",
    )
    return InvestigationReport(**{**base, **overrides})


class TestInvestigationReportRendering:
    """`delegate_task` returns str(output); this rendering is what the coordinator reads."""

    def test_complete_report_says_nothing_was_dropped(self):
        text = str(_report())

        assert "**Answer**: The service is paused for billing." in text
        assert "- deployer state Paused" in text
        assert "**Confidence**: high" in text
        assert "**Dropped steps**: none" in text
        assert "Budget exhausted" not in text

    def test_dropped_steps_carry_what_was_tried(self):
        text = str(
            _report(
                dropped_steps=[
                    DroppedStep(
                        step="check replication lag",
                        reason="budget reached",
                        tried=["thanos-range-query", "es_logs"],
                        suggested_next="query pg_stat_replication directly",
                    )
                ],
                budget_exhausted=True,
            )
        )

        assert "- check replication lag — budget reached" in text
        assert "tried: thanos-range-query; es_logs" in text
        assert "suggested next: query pg_stat_replication directly" in text
        assert "**Budget exhausted**: yes" in text

    def test_empty_evidence_is_explicit(self):
        assert "- none" in str(_report(evidence=[]))

    def test_round_trips_through_validation(self):
        report = _report(dropped_steps=[{"step": "s", "reason": "r"}])

        assert InvestigationReport.model_validate(report.model_dump()) == report
