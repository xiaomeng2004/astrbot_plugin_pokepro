import asyncio
import json
from pathlib import Path
import random
import re
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register
from astrbot.api.message_components import Poke, Plain, Image, At, Face
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.api import logger

from typing import List, Union
from collections import namedtuple

# 定义响应选项结构体
ResponseOption = namedtuple("ResponseOption", ["handler", "args"])


@register(
    "戳一戳专业版",
    "Zhalslar",
    "【更专业的戳一戳插件】支持触发（反戳：文本：emoji：图库：meme：禁言：开盒：戳@某人）",
    "1.0.1",
    "https://github.com/Zhalslar/astrbot_plugin_pokepro",
)
class PokeproPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 获取所有 _respond 方法
        self.response_handlers = {
            "poke_respond": self.poke_respond,
            "llm_respond": self.llm_respond,
            "face_respond": self.face_respond,
            "gallery_respond": self.gallery_respond,
            "meme_respond": self.meme_respond,
            "ban_respond": self.ban_respond,
            "box_respond": self.box_respond,
        }

        # 初始化权重列表
        weight_str = config.get("weight_str", "")
        weight_list: list[int] = self._string_to_list(weight_str, "int")  # type: ignore

        # 如果权重数量不足，默认填充为 1
        self.weights: list[int] = weight_list + [1] * (
            len(self.response_handlers) - len(weight_list)
        )
        self.poke_max_times: int = config.get("poke_max_times", 5)

        # 表情ID列表
        face_ids_str = config.get("face_ids_str", "")
        self.face_ids: list[int] = self._string_to_list(face_ids_str, "int")  # type: ignore

        self.poke_interval: float = config.get("poke_interval", 0)

        # 戳一戳图库路径
        self.gallery_path: Path = Path(config.get("gallery_path", ""))
        self.gallery_path.mkdir(parents=True, exist_ok=True)

        # meme命令列表
        self.meme_cmds_str = config.get("meme_cmds_str", "")
        self.meme_cmds: list[str] = self._string_to_list(self.meme_cmds_str, "str")  # type: ignore

        # 禁言回复文本列表
        self.ban_responses: list[str] = config.get("ban_responses", [])

        # 禁言失败回复文本列表
        self.ban_fail_responses: list[str] = config.get("ban_fail_responses", [])

        # llm提示模板
        self.llm_prompt_template = config.get("llm_prompt_template", "")

    def _string_to_list(
        self,
        input_str: str,
        return_type: str = "str",
        sep: Union[str, list[str]] = [":", "：", ",", "，"],
    ) -> List[Union[str, int]]:
        """
        将字符串转换为列表，支持自定义一个或多个分隔符和返回类型。

        参数：
            input_str (str): 输入字符串。
            return_type (str): 返回类型，'str' 或 'int'。
            sep (Union[str, List[str]]): 一个或多个分隔符，默认为 [":", "；", ",", "，"]。
        返回：
            List[Union[str, int]]
        """
        # 如果sep是列表，则创建一个包含所有分隔符的正则表达式模式
        if isinstance(sep, list):
            pattern = "|".join(map(re.escape, sep))
        else:
            # 如果sep是单个字符，则直接使用
            pattern = re.escape(sep)

        parts = [p.strip() for p in re.split(pattern, input_str) if p.strip()]

        if return_type == "int":
            try:
                return [int(p) for p in parts]
            except ValueError as e:
                raise ValueError(f"转换失败 - 无效的整数: {e}")
        elif return_type == "str":
            return parts
        else:
            raise ValueError("return_type 必须是 'str' 或 'int'")

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_poke(self, event: AiocqhttpMessageEvent):
        """监听并响应戳一戳事件"""
        # 检查事件结构是否符合预期
        if (
            not hasattr(event, "message_obj")
            or not hasattr(event.message_obj, "message")
            or not event.message_obj.message
        ):
            return

        # 确保所有消息组件都是 Poke 类型且目标是自己
        for comp in event.message_obj.message:
            if not isinstance(comp, Poke):
                return
            if str(comp.qq) != event.get_self_id():
                return

        # 构建响应选项
        response_options = []
        for handler_name, handler_func in self.response_handlers.items():
            # 可根据 handler_name 设置特定参数，这里统一使用空字典
            response_options.append(ResponseOption(handler_func, {}))

        # 随机选择一个响应函数
        selected: ResponseOption = random.choices(
            population=response_options, weights=self.weights, k=1
        )[0]

        logger.debug(f"Selected poke response function: {selected.handler.__name__}")

        try:
            if asyncio.iscoroutinefunction(selected.handler):
                await selected.handler(event, **selected.args)
            else:
                selected.handler(event, **selected.args)
        except Exception as e:
            logger.error(f"执行戳一戳响应失败: {e}", exc_info=True)

    # ========== 响应函数 ==========
    async def poke_respond(self, event: AiocqhttpMessageEvent):
        """反戳"""
        client = event.bot
        group_id = event.get_group_id()
        send_id = event.get_sender_id()
        if group_id:
            for _ in range(random.randint(1, self.poke_max_times)):
                await client.group_poke(group_id=int(group_id), user_id=int(send_id))
                await asyncio.sleep(self.poke_interval)
        else:
            for _ in range(random.randint(1, self.poke_max_times)):
                await client.poke(user_id=int(send_id))
                await asyncio.sleep(self.poke_interval)
        event.stop_event()

    async def llm_respond(self, event: AiocqhttpMessageEvent):
        """调用llm回复"""
        try:
            umo = event.unified_msg_origin
            curr_cid = await self.context.conversation_manager.get_curr_conversation_id(umo)
            conversation = await self.context.conversation_manager.get_conversation(umo, curr_cid)
            contexts = json.loads(conversation.history)

            personality = self.context.get_using_provider().curr_personality
            personality_prompt = personality["prompt"] if personality else ""

            format_prompt = self.llm_prompt_template.format(
                username=event.get_sender_name()
            )

            llm_response = await self.context.get_using_provider().text_chat(
                prompt=format_prompt,
                system_prompt=personality_prompt,
                contexts=contexts,
            )

        except Exception as e:
            logger.error(f"LLM 调用失败：{e}")
            return

        await event.send(MessageChain(chain=[Plain(llm_response.completion_text)]))  # type: ignore
        event.stop_event()

    async def face_respond(self, event: AiocqhttpMessageEvent):
        """回复emojji(QQ表情)"""
        face_id = random.choice(self.face_ids) if self.face_ids else 287
        faces_chain: list[Face] = [Face(id=face_id)] * random.randint(1, 3)
        await event.send(MessageChain(chain=faces_chain))  # type: ignore
        event.stop_event()

    async def gallery_respond(self, event: AiocqhttpMessageEvent):
        """调用图库进行回复"""
        files = list(self.gallery_path.iterdir())
        if not files:
            return
        selected_file = str(random.choice(files))
        await event.send(MessageChain(chain=[Image(selected_file)]))  # type: ignore
        event.stop_event()

    async def meme_respond(self, event: AiocqhttpMessageEvent):
        """回复合成的meme"""
        await self.send_cmd(event, random.choice(self.meme_cmds))

    async def ban_respond(self, event: AiocqhttpMessageEvent):
        """禁言"""
        try:
            await event.bot.set_group_ban(
                group_id=int(event.get_group_id()),
                user_id=int(event.get_sender_id()),
                duration=random.randint(30, 120),
            )
            reply_list = self.ban_responses

        except Exception:
            reply_list = self.ban_fail_responses

        reply = await self.format_reply(event, random.choice(reply_list))
        await event.send(MessageChain(chain=[Plain(reply)]))  # type: ignore
        event.stop_event()

    async def box_respond(self, event: AiocqhttpMessageEvent):
        """开盒"""
        await self.send_cmd(event, "盒")

    async def send_cmd(self, event: AiocqhttpMessageEvent, command: str):
        """发送命令"""
        self_id = event.get_self_id()
        obj_msg = event.message_obj.message
        obj_msg.clear()
        obj_msg.append(At(qq=self_id))
        obj_msg.append(Plain(command))
        event.message_obj.message_str = command
        event.message_str = command
        self.context.get_event_queue().put_nowait(event)
        event.should_call_llm(True)

    async def format_reply(self, event: AiocqhttpMessageEvent, template: str) -> str:
        """格式化回复模板，仅在需要时获取用户名或机器人名"""

        client = event.bot
        send_id = event.get_sender_id()
        self_id = event.get_self_id()
        username = "你"
        botname = "我"

        if "{username}" in template:
            user_info = await client.get_stranger_info(
                user_id=int(send_id), no_cache=True
            )
            username = user_info.get("nickname", "你")

        if "{botname}" in template:
            bot_info = await client.get_stranger_info(
                user_id=int(self_id), no_cache=True
            )
            botname = bot_info.get("nickname", "我")

        return template.format(botname=botname, username=username)

    @filter.command("戳", alias={"戳我"})
    async def poke_handle(self, event: AiocqhttpMessageEvent, times: int = 1):
        """戳@xxx / 戳我"""
        target_ids = [
            str(seg.qq)
            for seg in event.get_messages()
            if isinstance(seg, At) and str(seg.qq) != event.get_self_id()
        ]
        if event.message_str == "戳我":
            target_ids.append(event.get_sender_id())
        if not target_ids:
            return

        group_id = event.get_group_id()

        try:
            for target_id in target_ids:
                if group_id:
                    for _ in range(times):
                        await event.bot.group_poke(
                            group_id=int(group_id), user_id=int(target_id)
                        )
                        await asyncio.sleep(self.poke_interval)
                else:
                    for _ in range(times):
                        await event.bot.friend_poke(user_id=int(target_id))
                        await asyncio.sleep(self.poke_interval)
        except Exception as e:
            logger.error(f"发送戳一戳失败：{e}")
