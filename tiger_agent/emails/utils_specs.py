from pathlib import Path

import pytest

from tiger_agent.emails.utils import (
    html_to_slack_mrkdwn,
    is_forward_subject,
    plain_text_to_slack_mrkdwn,
)

# Bodies from case 00045773: a Gmail reply and the support email it answers.
GMAIL_REPLY_HTML = (
    '<div dir="auto">Hello!!</div><div><br><div class="gmail_quote gmail_quote_container">'
    '<div dir="ltr" class="gmail_attr">On Thu, Nov 5, 2026 at 10:24 AM Tiger Data Support '
    '&lt;<a href="mailto:support@tigerdata.com">support@tigerdata.com</a>&gt; wrote:<br></div>'
    '<blockquote class="gmail_quote" style="margin:0px 0px 0px 0.8ex"><div>Hello!</div>'
    '<img alt="" src="https://timescale.my.salesforce.com/servlet/servlet.ImageServer?oid=x">'
    "<br><br>ref:!00D6A02Idfc.!500Nv0nnbzw:ref</blockquote></div></div>\n\n<p></p>\n\n"
    "To unsubscribe from this group and stop receiving emails from it, send an email to "
    '<a href="mailto:support+unsubscribe@tigerdata.com">support+unsubscribe@tigerdata.com</a>.<br />'
)
SUPPORT_EMAIL_HTML = (
    '<html><div>Hello!</div><img alt="" src="https://timescale.my.salesforce.com/x">'
    "<br><br>ref:!00D6A02Idfc.!500Nv0nnbzw:ref</html>"
)
GMAIL_REPLY_TEXT = (
    "Hello!!\n\nOn Thu, Nov 5, 2026 at 10:24 AM Tiger Data Support <\n"
    "support@tigerdata.com> wrote:\n\n> Hello!\n>\n> ref:!00D6A02Idfc.!500Nv0nnbzw:ref\n\n"
    "To unsubscribe from this group and stop receiving emails from it, send an email to\n"
    "support+unsubscribe@tigerdata.com.\n"
)


class TestHtmlToSlackMrkdwn:
    def test_drops_the_gmail_quote_and_group_footer(self):
        assert html_to_slack_mrkdwn(GMAIL_REPLY_HTML) == "Hello!!"

    def test_drops_the_thread_reference_token(self):
        assert html_to_slack_mrkdwn(SUPPORT_EMAIL_HTML) == "Hello!"

    def test_keeps_line_breaks_between_blocks(self):
        html = (
            "<div>Hi team,</div><div><br></div><div>Line one</div><div>Line two</div>"
            "<p>A paragraph</p><p>Another</p>"
        )
        assert (
            html_to_slack_mrkdwn(html)
            == "Hi team,\n\nLine one\nLine two\n\nA paragraph\n\nAnother"
        )

    def test_keeps_br_line_breaks(self):
        assert html_to_slack_mrkdwn("Thanks,<br>\nBob") == "Thanks,\nBob"

    def test_renders_inline_formatting_links_and_lists(self):
        html = (
            '<p><b>Bold</b> <i>it</i> <code>x</code> <a href="https://x.com">docs</a> '
            '<a href="https://y.com">https://y.com</a></p><ul><li>a</li><li>b</li></ul>'
        )
        assert (
            html_to_slack_mrkdwn(html)
            == "*Bold* _it_ `x` <https://x.com|docs> <https://y.com>\n\n• a\n• b"
        )

    def test_escapes_slack_control_characters(self):
        assert (
            html_to_slack_mrkdwn("<p>a &lt;b&gt; &amp; c</p>") == "a &lt;b&gt; &amp; c"
        )

    def test_cuts_at_outlook_reply_header(self):
        html = (
            '<div>New text</div><hr><div id="divRplyFwdMsg"><b>From:</b> Support</div>'
            "<div>old text</div>"
        )
        assert html_to_slack_mrkdwn(html) == "New text"

    def test_cuts_at_outlook_header_without_a_space_after_the_label(self):
        html = (
            "<p>Any update?</p><p><b>From:</b> Lisa<br><b>Sent:</b>Monday<br>"
            "<b>To:</b> support</p><p>old</p>"
        )
        assert html_to_slack_mrkdwn(html) == "Any update?"

    def test_cuts_at_original_message_separator(self):
        html = "<p>New text</p><p>-----Original Message-----</p><p>old</p>"
        assert html_to_slack_mrkdwn(html) == "New text"

    def test_renders_a_blockquote_that_is_not_a_reply(self):
        assert (
            html_to_slack_mrkdwn("<p>See:</p><blockquote>quoted bit</blockquote>")
            == "See:\n\n> quoted bit"
        )


class TestIsForwardSubject:
    @pytest.mark.parametrize("subject", ["Fwd: Invoice", "FW: Invoice", "fw:Invoice"])
    def test_matches_forward_prefixes(self, subject):
        assert is_forward_subject(subject)

    @pytest.mark.parametrize("subject", ["Re: FW: Invoice", "Invoice", None])
    def test_ignores_replies_and_plain_subjects(self, subject):
        assert not is_forward_subject(subject)


class TestPlainTextToSlackMrkdwn:
    def test_drops_the_wrapped_reply_header_and_quote(self):
        assert plain_text_to_slack_mrkdwn(GMAIL_REPLY_TEXT) == "Hello!!"

    def test_keeps_newlines(self):
        assert plain_text_to_slack_mrkdwn("a\r\nb\n\n\n\nc") == "a\nb\n\nc"

    def test_cuts_at_outlook_from_sent_block(self):
        text = "New\n\nFrom: Support <s@x.com>\nSent: Thursday\nTo: me\n\nold"
        assert plain_text_to_slack_mrkdwn(text) == "New"


# Each fixture mirrors the HTML (or plain text) a real mail client sent to a
# support case, with the names and wording replaced. ``<name>.html`` or
# ``<name>.text`` is the email body and ``<name>.slack`` the expected Slack
# mrkdwn. Fixtures named ``*forward*`` are rendered as forwards.
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "source",
    sorted([*FIXTURES.glob("*.html"), *FIXTURES.glob("*.text")]),
    ids=lambda path: path.name,
)
def test_renders_email_fixture(source: Path):
    body = source.read_text()
    expected = source.with_suffix(".slack").read_text().rstrip("\n")
    forwarded = "forward" in source.stem
    if source.suffix == ".html":
        assert html_to_slack_mrkdwn(body, forwarded=forwarded) == expected
    else:
        assert plain_text_to_slack_mrkdwn(body, forwarded=forwarded) == expected
