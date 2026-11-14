from collections.abc import Awaitable, Callable
from typing import cast

from app.services.runtime import CommandRuntimeView
from domain.routing.plain_music_commands import plain_music_command

from .command_message_service import MessageContext


class CommandRouter:
    def __init__(self, runtime: CommandRuntimeView):
        self._runtime = runtime
        self._services = runtime.services
        self._bot_mention = runtime.bot_mention

    async def route(self, ctx: MessageContext) -> None:
        if ctx.is_mention_command(self._bot_mention):
            await self._route_mention(ctx)
            return

        if ctx.is_slash_command():
            await self._route_slash(ctx)
            return

        if ctx.content.strip() in ('1', '2', '3', '4', '5'):
            choose = getattr(self._services.interaction.music, 'handle_voice_pick', None)
            callback = cast(Callable[[str, str, str, str], Awaitable[bool]], choose)
            if callable(choose) and await callback(ctx.content, ctx.channel, ctx.area, ctx.user) is True:
                await self._services.safety.recall_scheduler.schedule_user_message_recall(
                    ctx.message_id, ctx.channel, ctx.area, ctx.timestamp,
                )
                return

        if getattr(self._runtime.plugins, "matches_plain_command", lambda _: False)(ctx.content) is True:
            await self._services.routing.mention.dispatch(ctx.content, ctx.channel, ctx.area, ctx.user)
            await self._services.safety.recall_scheduler.schedule_user_message_recall(
                ctx.message_id, ctx.channel, ctx.area, ctx.timestamp,
            )
            return

        text = plain_music_command(ctx.content)
        if text:
            await self._services.interaction.music.handle_mention(
                text, ctx.channel, ctx.area, ctx.user,
            )
            await self._services.safety.recall_scheduler.schedule_user_message_recall(
                ctx.message_id, ctx.channel, ctx.area, ctx.timestamp,
            )
            return

        await self._route_chat(ctx)

    async def _route_mention(self, ctx: MessageContext) -> None:
        text = ctx.mention_text(self._bot_mention)
        if text:
            await self._services.routing.mention.dispatch(
                text,
                ctx.channel,
                ctx.area,
                ctx.user,
            )
        await self._services.safety.recall_scheduler.schedule_user_message_recall(
            ctx.message_id,
            ctx.channel,
            ctx.area,
            ctx.timestamp,
        )

    async def _route_slash(self, ctx: MessageContext) -> None:
        await self._services.routing.slash.dispatch(ctx.content, ctx.channel, ctx.area, ctx.user)
        await self._services.safety.recall_scheduler.schedule_user_message_recall(
            ctx.message_id,
            ctx.channel,
            ctx.area,
            ctx.timestamp,
        )

    async def _route_chat(self, ctx: MessageContext) -> None:
        await self._services.interaction.chat.handle_plain_chat(
            ctx.content,
            ctx.channel,
            ctx.area,
        )
