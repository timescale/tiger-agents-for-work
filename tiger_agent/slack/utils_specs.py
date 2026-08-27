from tiger_agent.slack.utils import (
    get_channel_link,
    get_handle_link,
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
