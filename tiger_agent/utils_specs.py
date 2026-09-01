from tiger_agent.utils import split_markdown_text_into_blocks


class TestSplitMarkdownTextIntoBlocks:
    def test_empty_string_returns_empty_list(self):
        assert split_markdown_text_into_blocks("", max_string_length=100) == []

    def test_text_shorter_than_cap_returns_single_chunk(self):
        text = "hello world"
        assert split_markdown_text_into_blocks(text, max_string_length=100) == [text]

    def test_text_exactly_at_cap_returns_single_chunk(self):
        text = "a" * 50
        assert split_markdown_text_into_blocks(text, max_string_length=50) == [text]

    def test_two_paragraphs_fitting_together_stay_in_one_chunk(self):
        text = "first paragraph\n\nsecond paragraph"
        result = split_markdown_text_into_blocks(text, max_string_length=100)
        # A single chunk should preserve the "\n\n" separator between paragraphs.
        assert result == [text]

    def test_two_paragraphs_split_when_second_wont_fit_preserves_content(self):
        # Even when the two paragraphs go to different chunks, joining chunks
        # back with "\n\n" must reproduce the input verbatim.
        text = "first paragraph\n\nsecond paragraph"
        result = split_markdown_text_into_blocks(text, max_string_length=20)
        assert "\n\n".join(result) == text

    def test_two_paragraphs_split_when_second_wont_fit(self):
        text = "first paragraph\n\nsecond paragraph"
        result = split_markdown_text_into_blocks(text, max_string_length=20)
        assert result == ["first paragraph", "second paragraph"]

    def test_three_paragraphs_split_at_paragraph_boundaries(self):
        text = "aaaa\n\nbbbb\n\ncccc"
        result = split_markdown_text_into_blocks(text, max_string_length=10)
        # "aaaa\n\nbbbb" is 10 chars, exactly the cap. "cccc" opens the next chunk.
        assert result == ["aaaa\n\nbbbb", "cccc"]

    def test_chunk_length_never_exceeds_cap(self):
        paragraphs = [f"paragraph number {i} with some content" for i in range(50)]
        text = "\n\n".join(paragraphs)
        max_len = 100
        result = split_markdown_text_into_blocks(text, max_string_length=max_len)
        for chunk in result:
            assert len(chunk) <= max_len

    def test_no_content_is_lost_across_chunks(self):
        paragraphs = [f"paragraph {i}" for i in range(20)]
        text = "\n\n".join(paragraphs)
        result = split_markdown_text_into_blocks(text, max_string_length=50)
        # Rejoin the chunks with the same separator the function uses internally.
        rejoined = "\n\n".join(result)
        assert rejoined == text

    def test_trailing_double_newline_does_not_produce_empty_chunk(self):
        text = "hello\n\n"
        result = split_markdown_text_into_blocks(text, max_string_length=100)
        assert "" not in result
        assert result == ["hello\n\n"]

    def test_input_without_paragraph_breaks_returns_one_chunk_when_under_cap(self):
        text = "line one\nline two\nline three"
        result = split_markdown_text_into_blocks(text, max_string_length=100)
        assert result == [text]

    def test_single_section_larger_than_cap_is_not_further_split(self):
        # Known limitation: the chunker does not further split a paragraph
        # that is itself larger than the cap. It should return the section
        # unchanged as its own chunk (Slack will reject it, but that's a
        # separate concern from content preservation).
        text = "x" * 200
        result = split_markdown_text_into_blocks(text, max_string_length=50)
        assert result == [text]

    def test_no_empty_chunks_are_emitted(self):
        # A leading oversized section previously produced an empty chunk
        # at the head of the result. Guard against that regression.
        text = "x" * 200 + "\n\nnormal paragraph"
        result = split_markdown_text_into_blocks(text, max_string_length=50)
        assert "" not in result

    def test_padding_between_paragraphs_is_only_added_when_needed(self):
        # First chunk should not start with "\n\n"; second chunk should not either.
        text = "aaaa\n\nbbbb\n\ncccc"
        result = split_markdown_text_into_blocks(text, max_string_length=6)
        for chunk in result:
            assert not chunk.startswith("\n\n")
