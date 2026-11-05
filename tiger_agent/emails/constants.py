"""Markers mail clients use for quoted history and footers, and the HTML tags
the Slack mrkdwn conversion treats specially."""

import re

# Element classes/ids that mail clients put around the quoted earlier message.
# Gmail also marks quotes the sender places inline between their own answers
# with a bare <blockquote class="gmail_quote">, so that class only counts on
# other elements.
QUOTE_CLASSES = {
    "gmail_quote_container",
    "gmail_attr",
    "yahoo_quoted",
    "moz-cite-prefix",
}
NON_BLOCKQUOTE_QUOTE_CLASSES = {"gmail_quote"}
QUOTE_IDS = {
    "divRplyFwdMsg",
    "appendonsend",
    "mail-editor-reference-message-container",
}
# Spark wraps its "On ... wrote:" line and the quote in a named section.
QUOTE_NAMES = {"messageReplySection"}
EMPHASIS_MARKERS = {
    "b": "*",
    "strong": "*",
    "i": "_",
    "em": "_",
    "s": "~",
    "strike": "~",
    "del": "~",
    "code": "`",
}

BLOCK_TAGS = {
    "address",
    "article",
    "blockquote",
    "dd",
    "div",
    "dl",
    "dt",
    "footer",
    "header",
    "hr",
    "li",
    "ol",
    "section",
    "table",
    "tr",
    "ul",
}
PARAGRAPH_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "pre"}
SKIP_TAGS = {"head", "script", "style", "title"}

# Words mail clients use in the header of a quoted message, by language.
HEADER_FROM = r"(?:From|Van|Von|De|Da|Från|Fra|Od)"
HEADER_SENT = r"(?:Sent|Date|Verzonden|Gesendet|Datum|Envoyé|Inviato|Enviado|Skickat|Sendt|Wysłano)"

# Text that begins the quoted history, in either format.
REPLY_PATTERNS = [
    re.compile(r"-{3,}\s*Original Message\s*-{3,}", re.IGNORECASE),
    # "On <date>, <name> <addr> wrote:", which plain-text clients wrap.
    re.compile(r"^\s*On\s[^\n]*(?:\n[^\n]*){0,2}?\swrote:\s*$", re.MULTILINE),
    # Outlook's "From: ... / Sent: ..." block, bold when rendered from HTML.
    re.compile(
        rf"^\s*\*?{HEADER_FROM}:\*?\s[^\n]*\n\s*\*?{HEADER_SENT}:\*?",
        re.MULTILINE,
    ),
]
# Mailing-list footers appended after the message.
FOOTER_PATTERNS = [
    re.compile(r"^\s*To unsubscribe from this group", re.IGNORECASE | re.MULTILINE),
    re.compile(
        r"^\s*You received this message because you are subscribed to",
        re.IGNORECASE | re.MULTILINE,
    ),
]
# A forward carries the forwarded email as its content, so nothing is cut. Only
# the subject says so: replies to a forward also carry a forwarded-message
# header in their quoted history.
FORWARD_SUBJECT = re.compile(r"^\s*(?:Fwd?|FW|WG|TR|RV|I)\s*:", re.IGNORECASE)

# Plain-text clients quote the earlier message with "> " lines.
PLAIN_TEXT_REPLY_PATTERNS = [re.compile(r"^>", re.MULTILINE)]
# Salesforce Email-to-Case adds a thread token to the emails it sends, e.g. ref:!00D6A02Idfc.!500Nv0nnbzw:ref
SALESFORCE_THREAD_REF = re.compile(r"\s*ref:![\w.!]+:ref\s*")
