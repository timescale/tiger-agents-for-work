from tiger_agent.slack.utils import (
    get_channel_link,
    get_handle_link,
    post_response,
    user_is_external,
)


class TestGetHandleLink:
    def test_wraps_user_id_in_slack_mention_syntax(self):
        assert get_handle_link("U12345") == "<@U12345>"


class TestGetChannelLink:
    def test_wraps_channel_id_in_slack_channel_syntax(self):
        assert get_channel_link("C98765") == "<#C98765>"


class TestUserIsExternal:
    def test_same_team_regular_user_is_internal(self, make_bot_info, make_user_info):
        bot = make_bot_info(team_id="T_HOME")
        user = make_user_info(team_id="T_HOME")
        assert user_is_external(bot_info=bot, user_info=user) is False

    def test_different_team_is_external(self, make_bot_info, make_user_info):
        bot = make_bot_info(team_id="T_HOME")
        user = make_user_info(team_id="T_OTHER")
        assert user_is_external(bot_info=bot, user_info=user) is True

    def test_restricted_user_is_external_even_on_same_team(
        self, make_bot_info, make_user_info
    ):
        bot = make_bot_info(team_id="T_HOME")
        user = make_user_info(team_id="T_HOME", is_restricted=True)
        assert user_is_external(bot_info=bot, user_info=user) is True

    def test_ultra_restricted_user_is_external_even_on_same_team(
        self, make_bot_info, make_user_info
    ):
        bot = make_bot_info(team_id="T_HOME")
        user = make_user_info(team_id="T_HOME", is_ultra_restricted=True)
        assert user_is_external(bot_info=bot, user_info=user) is True

    def test_stranger_user_is_external_even_on_same_team(
        self, make_bot_info, make_user_info
    ):
        bot = make_bot_info(team_id="T_HOME")
        user = make_user_info(team_id="T_HOME", is_stranger=True)
        assert user_is_external(bot_info=bot, user_info=user) is True


class TestPostMessage:
    async def test_should_pass_all_fields_to_post_message(
        self, make_async_web_client_mock
    ):
        message = "this is a message"
        client = make_async_web_client_mock()
        await post_response(
            client=client,
            channel="channel",
            text=message,
            thread_ts="thread_ts",
        )

        assert client.chat_postMessage.call_count == 1
        client.chat_postMessage.assert_called_with(
            channel="channel",
            text=message,
            thread_ts="thread_ts",
            unfurl_links=False,
            unfurl_media=False,
        )

    async def test_should_split_large_markdown_text_into_multiple_calls(
        self, make_async_web_client_mock
    ):
        # 15 characters per line * 1000 = 15k, exceeds the max length for markdown
        # in a single message
        text = "## Heading" + "\nthis is a line\n\n" * 1000

        client = make_async_web_client_mock()
        await post_response(
            client=client,
            channel="channel",
            text=text,
            thread_ts="thread_ts",
        )

        assert client.chat_postMessage.call_count == 2
