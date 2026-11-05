"""Convert email bodies into Slack mrkdwn.

Email replies carry the whole conversation underneath the new text, so the
conversion cuts the body at the first reply marker and drops mailing-list
footers and Salesforce's thread reference token.
"""

import re
from html.parser import HTMLParser

from tiger_agent.emails.constants import (
    BLOCK_TAGS,
    EMPHASIS_MARKERS,
    FOOTER_PATTERNS,
    FORWARD_SUBJECT,
    NON_BLOCKQUOTE_QUOTE_CLASSES,
    PARAGRAPH_TAGS,
    PLAIN_TEXT_REPLY_PATTERNS,
    QUOTE_CLASSES,
    QUOTE_IDS,
    QUOTE_NAMES,
    REPLY_PATTERNS,
    SALESFORCE_THREAD_REF,
    SKIP_TAGS,
)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _truncate(text: str, patterns: list[re.Pattern[str]]) -> str:
    cut = len(text)
    for pattern in patterns:
        if match := pattern.search(text):
            cut = min(cut, match.start())
    return text[:cut]


def _tidy(text: str) -> str:
    text = SALESFORCE_THREAD_REF.sub("\n", text)
    lines = [line.rstrip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class _SlackMrkdwnConverter(HTMLParser):
    """Renders HTML as Slack mrkdwn, optionally stopping at the first quoted reply."""

    def __init__(self, stop_at_reply: bool) -> None:
        super().__init__(convert_charrefs=True)
        self._stop_at_reply = stop_at_reply
        self._parts: list[str] = []
        self._skip_depth = 0
        self._pre_depth = 0
        self._list_depth = 0
        self._quote_depth = 0
        self._link_stack: list[tuple[str | None, int]] = []
        self._emphasis_stack: list[tuple[str, int]] = []
        self.done = False

    def _newline(self, count: int = 1) -> None:
        self._parts.append("\n" * count)

    def _trailing_whitespace(self) -> str:
        """The whitespace at the end of the output so far."""
        tail = []
        for part in reversed(self._parts):
            stripped = part.rstrip()
            tail.append(part[len(stripped) :])
            if stripped:
                break
        return "".join(reversed(tail))

    def _break(self, count: int) -> None:
        """End the current block with at least ``count`` newlines.

        Adjacent blocks share a break, so ``</div><div>`` is one line break
        rather than two.
        """
        if not self._parts:
            return
        trailing = self._trailing_whitespace().count("\n")
        if count > trailing:
            self._newline(count - trailing)

    def _is_reply_marker(self, tag: str, attrs: dict[str, str | None]) -> bool:
        classes = set((attrs.get("class") or "").split())
        if tag != "blockquote" and classes & NON_BLOCKQUOTE_QUOTE_CLASSES:
            return True
        if classes & QUOTE_CLASSES or (attrs.get("id") or "") in QUOTE_IDS:
            return True
        if (attrs.get("name") or "") in QUOTE_NAMES:
            return True
        return tag == "blockquote" and attrs.get("type") == "cite"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.done:
            return
        attr_map = dict(attrs)
        if self._stop_at_reply and self._is_reply_marker(tag, attr_map):
            self.done = True
            return
        if tag == "body":
            # Some senders never close <head>; the body is never skipped.
            self._skip_depth = 0
        elif tag in SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "br":
            self._newline()
        elif tag in PARAGRAPH_TAGS:
            self._break(2)
            if tag == "pre":
                self._pre_depth += 1
                self._parts.append("```\n")
            elif tag.startswith("h"):
                self._emphasis_stack.append((tag, len(self._parts)))
        elif tag in BLOCK_TAGS:
            self._break(1)
            if tag in ("ul", "ol"):
                self._list_depth += 1
            elif tag == "li":
                self._parts.append("    " * max(self._list_depth - 1, 0) + "• ")
            elif tag == "blockquote":
                self._quote_depth += 1
        elif tag in EMPHASIS_MARKERS and not self._pre_depth:
            self._emphasis_stack.append((tag, len(self._parts)))
        elif tag == "a":
            self._link_stack.append((attr_map.get("href"), len(self._parts)))

    def handle_endtag(self, tag: str) -> None:
        if self.done:
            return
        if tag in SKIP_TAGS:
            self._skip_depth = max(self._skip_depth - 1, 0)
        elif tag in PARAGRAPH_TAGS:
            if tag == "pre":
                self._pre_depth = max(self._pre_depth - 1, 0)
                self._parts.append("\n```")
            elif tag.startswith("h"):
                self._close_emphasis(tag, "*")
            self._break(2)
        elif tag in BLOCK_TAGS:
            if tag in ("ul", "ol"):
                self._list_depth = max(self._list_depth - 1, 0)
            elif tag == "blockquote":
                self._quote_depth = max(self._quote_depth - 1, 0)
            self._break(1)
        elif tag in EMPHASIS_MARKERS and not self._pre_depth:
            self._close_emphasis(tag, EMPHASIS_MARKERS[tag])
        elif tag == "a" and self._link_stack:
            href, start = self._link_stack.pop()
            label = "".join(self._parts[start:]).strip()
            del self._parts[start:]
            if not label:
                # An image-only link, such as a signature logo.
                return
            if not href or href.startswith("#"):
                self._parts.append(label)
            elif label == href:
                self._parts.append(f"<{_escape(href)}>")
            else:
                self._parts.append(f"<{_escape(href)}|{label}>")

    def _close_emphasis(self, tag: str, marker: str) -> None:
        """Wrap the text since the matching start tag in ``marker``.

        Slack only renders a marker that hugs the text on one line, so each
        line is wrapped on its own, surrounding whitespace stays outside, and
        an element with no text gets no markers.
        """
        for index in range(len(self._emphasis_stack) - 1, -1, -1):
            if self._emphasis_stack[index][0] == tag:
                start = self._emphasis_stack[index][1]
                del self._emphasis_stack[index:]
                break
        else:
            return
        content = "".join(self._parts[start:])
        del self._parts[start:]
        wrapped = []
        for line in content.split("\n"):
            core = line.strip()
            if not core:
                wrapped.append(line)
                continue
            lead = line[: len(line) - len(line.lstrip())]
            trail = line[len(line.rstrip()) :]
            wrapped.append(f"{lead}{marker}{core}{marker}{trail}")
        self._parts.append("\n".join(wrapped))

    def handle_data(self, data: str) -> None:
        if self.done or self._skip_depth:
            return
        if not self._pre_depth:
            at_line_start = not self._parts or "\n" in self._trailing_whitespace()
            if "\xa0" in data and not data.strip() and at_line_start:
                # A line holding only &nbsp; is how mail clients write a blank line.
                self._newline()
                return
            data = re.sub(r"\s+", " ", data)
        data = _escape(data)
        if self._quote_depth and data.strip():
            data = data.lstrip()
            if not self._parts or self._parts[-1].endswith("\n"):
                data = "> " + data
        self._parts.append(data)

    def get_output(self) -> str:
        return "".join(self._parts)


def html_to_slack_mrkdwn(html: str, *, forwarded: bool = False) -> str:
    """Render an HTML message body as Slack mrkdwn.

    A reply loses its quoted history; a forward keeps it, since that is what
    was sent.
    """
    converter = _SlackMrkdwnConverter(stop_at_reply=not forwarded)
    converter.feed(html)
    converter.close()
    # Collapsing whitespace leaves a space at the start of lines that follow a
    # block break; strip it so the result reads as written.
    text = "\n".join(line.lstrip(" ") for line in converter.get_output().split("\n"))
    patterns = FOOTER_PATTERNS if forwarded else REPLY_PATTERNS + FOOTER_PATTERNS
    text = _truncate(text, patterns)
    # Replies that quote the customer inline with "> " lines read as quotes.
    text = re.sub(r"^&gt; ?", "> ", text, flags=re.MULTILINE)
    return _tidy(text)


def plain_text_to_slack_mrkdwn(text: str, *, forwarded: bool = False) -> str:
    """Render a plain-text message body as Slack mrkdwn.

    Quoted history is cut as in :func:`html_to_slack_mrkdwn`.
    """
    text = text.replace("\r\n", "\n")
    if forwarded:
        patterns = FOOTER_PATTERNS
    else:
        patterns = REPLY_PATTERNS + FOOTER_PATTERNS + PLAIN_TEXT_REPLY_PATTERNS
    return _tidy(_escape(_truncate(text, patterns)))


def is_forward_subject(subject: str | None) -> bool:
    """Whether an email subject marks the email as a forward."""
    return bool(FORWARD_SUBJECT.match(subject or ""))
