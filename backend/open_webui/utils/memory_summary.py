import json
import logging
import time
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Request
from starlette.datastructures import Headers
from starlette.responses import Response

from open_webui.config import (
    DEFAULT_MEMORY_CHAT_SUMMARY_PROMPT_TEMPLATE,
    DEFAULT_MEMORY_SUMMARY_PROMPT_TEMPLATE,
)
from open_webui.constants import TASKS
from open_webui.models.chats import Chats, ChatModel
from open_webui.models.users import Users, UserModel
from open_webui.routers.pipelines import process_pipeline_inlet_filter
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.misc import get_message_list
from open_webui.utils.models import check_model_access, get_all_models
from open_webui.utils.task import (
    get_task_model_id,
    prompt_template,
    replace_messages_variable,
)

log = logging.getLogger(__name__)

SECONDS_PER_DAY = 86400
MISFIRE_GRACE_TIME_SECONDS = 3600
USER_BATCH_SIZE = 100


def select_messages_for_summary(
    messages: list[dict],
    user_messages_only: bool,
    first_n_messages: int,
    last_n_messages: int,
) -> list[dict]:
    filtered_messages = (
        [message for message in messages if message.get("role") == "user"]
        if user_messages_only
        else list(messages)
    )

    if first_n_messages > 0 and last_n_messages > 0:
        if len(filtered_messages) > first_n_messages + last_n_messages:
            return (
                filtered_messages[:first_n_messages]
                + filtered_messages[-last_n_messages:]
            )
        return filtered_messages

    if first_n_messages > 0:
        return filtered_messages[:first_n_messages]

    if last_n_messages > 0:
        return filtered_messages[-last_n_messages:]

    return filtered_messages


def get_new_messages(messages: list[dict], last_message_id: Optional[str]) -> list[dict]:
    if not last_message_id:
        return messages

    for idx, message in enumerate(messages):
        if message.get("id") == last_message_id:
            return messages[idx + 1 :]

    return messages


def _build_internal_request(app) -> Request:
    return Request(
        {
            "type": "http",
            "asgi.version": "3.0",
            "asgi.spec_version": "2.0",
            "method": "GET",
            "path": "/internal",
            "query_string": b"",
            "headers": Headers({}).raw,
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 80),
            "scheme": "http",
            "app": app,
        }
    )


def _parse_schedule_time(time_value: str) -> tuple[int, int]:
    try:
        parsed_time = datetime.strptime((time_value or "").strip(), "%H:%M")
        return parsed_time.hour, parsed_time.minute
    except Exception:
        log.warning(
            "Invalid MEMORY_SUMMARY_TIME format '%s'. Expected HH:MM (e.g., 02:00). Using 02:00.",
            time_value,
        )
        return 2, 0


def _extract_completion_content(response) -> Optional[str]:
    if isinstance(response, Response):
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except Exception:
            return None
    elif isinstance(response, dict):
        payload = response
    else:
        return None

    choices = payload.get("choices") if isinstance(payload, dict) else None
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        return message.get("content")

    return None


def _build_token_params(model: dict, max_tokens: int) -> dict:
    return (
        {"max_tokens": max_tokens}
        if model.get("owned_by") == "ollama"
        else {"max_completion_tokens": max_tokens}
    )


def _build_chat_summary_prompt(
    template: str,
    messages: list[dict],
    chat_title: str,
    existing_summary: str,
    user: UserModel,
) -> str:
    prompt = template or DEFAULT_MEMORY_CHAT_SUMMARY_PROMPT_TEMPLATE
    prompt = prompt.replace("{{CHAT_TITLE}}", chat_title or "Untitled Chat")
    prompt = prompt.replace("{{EXISTING_SUMMARY}}", existing_summary or "")
    prompt = replace_messages_variable(prompt, messages)
    return prompt_template(prompt, user)


def _build_memory_summary_prompt(
    template: str,
    memory_summary: str,
    chat_summaries: str,
    user: UserModel,
) -> str:
    prompt = template or DEFAULT_MEMORY_SUMMARY_PROMPT_TEMPLATE
    prompt = prompt.replace("{{MEMORY_SUMMARY}}", memory_summary or "")
    prompt = prompt.replace("{{CHAT_SUMMARIES}}", chat_summaries or "")
    return prompt_template(prompt, user)


def _select_task_model_id(app, user: UserModel) -> Optional[str]:
    models = app.state.MODELS or {}
    if not models:
        return None

    default_models = (app.state.config.DEFAULT_MODELS or "").split(",")
    stripped_model = default_models[0].strip() if default_models else ""
    default_model_id = stripped_model if stripped_model else None
    if not default_model_id:
        default_model_id = next(iter(models.keys()), None)

    if not default_model_id:
        return None

    task_model_id = get_task_model_id(
        default_model_id,
        app.state.config.TASK_MODEL,
        app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    candidate_ids = [task_model_id]
    if task_model_id != default_model_id:
        candidate_ids.append(default_model_id)

    for model_id in candidate_ids:
        if model_id not in models:
            continue
        try:
            check_model_access(user, models[model_id])
            return model_id
        except Exception:
            continue

    return None


async def _generate_summary(
    request: Request,
    user: UserModel,
    model_id: str,
    prompt: str,
    metadata: dict,
) -> Optional[str]:
    models = request.app.state.MODELS
    if model_id not in models:
        return None

    max_tokens = (
        models[model_id].get("info", {}).get("params", {}).get("max_tokens", 1000)
    )

    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        **_build_token_params(models[model_id], max_tokens),
        "metadata": metadata,
    }

    payload = await process_pipeline_inlet_filter(request, payload, user, models)
    response = await generate_chat_completion(request, form_data=payload, user=user)
    return _extract_completion_content(response)


async def _summarize_chat(
    app,
    request: Request,
    user: UserModel,
    chat: ChatModel,
    model_id: str,
    now: int,
) -> Optional[str]:
    history = chat.chat.get("history", {})
    messages_map = history.get("messages", {})
    current_message_id = history.get("currentId")
    message_list = get_message_list(messages_map, current_message_id)

    if not message_list:
        return None

    memory_meta = (chat.meta or {}).get("memory", {})
    last_summary_id = memory_meta.get("summary_message_id")

    new_messages = get_new_messages(message_list, last_summary_id)
    if not new_messages:
        return None

    filtered_messages = select_messages_for_summary(
        new_messages,
        app.state.config.MEMORY_SUMMARY_USER_MESSAGES_ONLY,
        app.state.config.MEMORY_SUMMARY_FIRST_N_MESSAGES,
        app.state.config.MEMORY_SUMMARY_LAST_N_MESSAGES,
    )

    if not filtered_messages:
        return None

    template = app.state.config.MEMORY_CHAT_SUMMARY_PROMPT_TEMPLATE
    prompt = _build_chat_summary_prompt(
        template,
        filtered_messages,
        chat.title,
        memory_meta.get("summary", ""),
        user,
    )

    summary = await _generate_summary(
        request,
        user,
        model_id,
        prompt,
        {
            "task": str(TASKS.MEMORY_CHAT_SUMMARY),
            "chat_id": chat.id,
            "user_id": user.id,
        },
    )

    if not summary:
        return None

    memory_meta.update(
        {
            "summary": summary,
            "summary_message_id": message_list[-1].get("id") or current_message_id,
            "summary_updated_at": now,
        }
    )

    updated_meta = {**(chat.meta or {}), "memory": memory_meta}
    Chats.update_chat_meta_by_id(chat.id, updated_meta)

    return summary


async def generate_user_memory_summary(app, request: Request, user: UserModel) -> None:
    if not app.state.config.ENABLE_MEMORIES or not app.state.config.ENABLE_MEMORY_SUMMARY:
        return

    if not request.app.state.MODELS:
        await get_all_models(request, user=user)

    model_id = _select_task_model_id(app, user)
    if not model_id:
        log.info("Skipping memory summary for %s: no accessible models.", user.id)
        return

    now = int(time.time())
    recent_days = app.state.config.MEMORY_SUMMARY_RECENT_DAYS
    cutoff_timestamp = now - (recent_days * SECONDS_PER_DAY) if recent_days > 0 else None

    chats_response = Chats.get_chats_by_user_id(user.id)
    chat_summaries = []

    for chat in chats_response.items:
        if cutoff_timestamp and chat.updated_at < cutoff_timestamp:
            continue

        try:
            summary = await _summarize_chat(app, request, user, chat, model_id, now)
            if summary:
                chat_summaries.append(f"Chat: {chat.title}\nSummary: {summary}")
        except Exception as exc:
            log.exception("Failed to summarize chat %s: %s", chat.id, exc)

    if not chat_summaries:
        return

    user_record = Users.get_user_by_id(user.id)
    user_settings = user_record.settings or {} if user_record else {}
    memory_settings = user_settings.get("memory", {})
    existing_summary = memory_settings.get("summary", "")

    template = app.state.config.MEMORY_SUMMARY_PROMPT_TEMPLATE
    prompt = _build_memory_summary_prompt(
        template,
        existing_summary,
        "\n\n".join(chat_summaries),
        user,
    )

    summary = await _generate_summary(
        request,
        user,
        model_id,
        prompt,
        {"task": str(TASKS.MEMORY_SUMMARY), "user_id": user.id},
    )

    if not summary:
        return

    memory_settings.update({"summary": summary, "summary_updated_at": now})
    Users.update_user_settings_by_id(user.id, {"memory": memory_settings})


async def run_memory_summary_job(app) -> None:
    if not app.state.config.ENABLE_MEMORIES or not app.state.config.ENABLE_MEMORY_SUMMARY:
        return

    request = _build_internal_request(app)
    if not app.state.MODELS:
        await get_all_models(request, user=None)

    skip = 0
    while True:
        batch = Users.get_users(skip=skip, limit=USER_BATCH_SIZE)
        users = batch.get("users", [])
        if not users:
            break

        for user in users:
            try:
                await generate_user_memory_summary(app, request, user)
            except Exception as exc:
                log.exception(
                    "Memory summary job failed for user %s: %s", user.id, exc
                )

        skip += USER_BATCH_SIZE


def setup_memory_summary_scheduler(app) -> Optional[AsyncIOScheduler]:
    if not app.state.config.ENABLE_MEMORY_SUMMARY:
        return None

    schedule = (app.state.config.MEMORY_SUMMARY_SCHEDULE or "").lower()
    if schedule not in ("daily", "weekly"):
        log.info(
            "Memory summary scheduler disabled (schedule=%s). Valid options are 'daily' or 'weekly'.",
            schedule,
        )
        return None

    hour, minute = _parse_schedule_time(app.state.config.MEMORY_SUMMARY_TIME)
    if schedule == "weekly":
        trigger = CronTrigger(
            day_of_week=app.state.config.MEMORY_SUMMARY_WEEKDAY or "sun",
            hour=hour,
            minute=minute,
        )
    else:
        trigger = CronTrigger(hour=hour, minute=minute)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_memory_summary_job,
        trigger=trigger,
        args=[app],
        id="memory_summary",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=MISFIRE_GRACE_TIME_SECONDS,
    )
    scheduler.start()
    log.info("Memory summary scheduler started (%s at %02d:%02d).", schedule, hour, minute)
    return scheduler
