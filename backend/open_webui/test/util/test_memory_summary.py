from open_webui.utils.memory_summary import get_new_messages, select_messages_for_summary


def test_select_messages_for_summary_user_only_and_trim():
    messages = [
        {"id": "1", "role": "user", "content": "alpha"},
        {"id": "2", "role": "assistant", "content": "beta"},
        {"id": "3", "role": "user", "content": "gamma"},
        {"id": "4", "role": "user", "content": "delta"},
    ]

    trimmed = select_messages_for_summary(
        messages, user_messages_only=True, first_n_messages=1, last_n_messages=1
    )

    assert [message["id"] for message in trimmed] == ["1", "4"]


def test_get_new_messages_after_id():
    messages = [
        {"id": "1", "role": "user", "content": "alpha"},
        {"id": "2", "role": "assistant", "content": "beta"},
        {"id": "3", "role": "user", "content": "gamma"},
    ]

    new_messages = get_new_messages(messages, "2")
    assert [message["id"] for message in new_messages] == ["3"]

    new_messages = get_new_messages(messages, "missing")
    assert [message["id"] for message in new_messages] == ["1", "2", "3"]
