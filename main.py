import asyncio
import json
from pathlib import Path
import random
import re
import time
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register
from astrbot.api.message_components import Plain, Image, At, Face, Poke
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.api import logger

from typing import List, Union


@register(
    "astrbot_plugin_pokepro",
    "Zhalslar",
    "【更专业的戳一戳插件】支持触发（反戳：文本：emoji：图库：meme：禁言：开盒：戳@某人）",
    "1.0.8",
    "https://github.com/Zhalslar/astrbot_plugin_pokepro",
)
class PokeproPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # 获取所有 _respond 方法（反戳：LLM：face：图库：禁言：meme：api：开盒）
        self.response_handlers = [
            self.poke_respond,
            self.llm_respond,
            self.face_respond,
            self.gallery_respond,
            self.ban_respond,
            self.meme_respond,
            self.api_respond,
            self.box_respond,
        ]

        # 初始化权重列表
        weight_str = config.get("weight_str", "")
        weight_list: list[int] = self._string_to_list(weight_str, "int")  # type: ignore
        self.weights: list[int] = weight_list + [1] * (
            len(self.response_handlers) - len(weight_list)
        )
        # 连戳最大次数
        self.poke_max_times: int = config.get("poke_max_times", 5)

        # 冷却时间
        self.cooldown_seconds = config.get("cooldown_seconds", 10)
        # 记录每个 user_id 的最后触发时间
        self.last_trigger_time = {}

        # 跟戳概率
        self.follow_poke_th: float = config.get("follow_poke_th", 0.05)

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

        # api命令列表
        self.api_cmds_str = config.get("api_cmds_str", "")
        self.api_cmds: list[str] = self._string_to_list(self.api_cmds_str, "str")  # type: ignore

        # 被戳llm提示模板
        self.llm_prompt_template = config.get("llm_prompt_template", "?")
        # 禁言提示模板
        self.ban_prompt_template = config.get("ban_prompt_template", "?")
        # 禁言失败提示模板
        self.ban_fail_prompt_template = config.get("ban_fail_prompt_template", "?")

        # 随机禁言时间范围
        ban_time_range_str = config.get("ban_time_range_str", "30~300")
        self.ban_time_range = tuple(map(int, ban_time_range_str.split("~")))

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

    async def _send_cmd(self, event: AiocqhttpMessageEvent, command: str):
        """发送命令，附带完整用户信息"""
        obj_msg = event.message_obj.message
        obj_msg.clear()
        
        # 构建消息链，包含@bot + 命令 + @用户
        message_chain = [At(qq=event.get_self_id()), Plain(f"{command} ")]
        
        # 添加发送者的@信息，让meme插件能够获取完整用户信息
        sender_id = event.get_sender_id()
        if sender_id and sender_id != event.get_self_id():
            message_chain.append(At(qq=sender_id))
            
        obj_msg.extend(message_chain)
        event.message_obj.message_str = command
        event.message_str = command
        self.context.get_event_queue().put_nowait(event)
        event.should_call_llm(True)

    async def _get_llm_respond(
        self, event: AiocqhttpMessageEvent, prompt_template: str
    ) -> str | None:
        """调用llm回复"""
        try:
            umo = event.unified_msg_origin
            curr_cid = await self.context.conversation_manager.get_curr_conversation_id(
                umo
            )
            conversation = await self.context.conversation_manager.get_conversation(
                umo, curr_cid
            )
            contexts = json.loads(conversation.history)

            personality = self.context.get_using_provider().curr_personality
            personality_prompt = personality["prompt"] if personality else ""

            format_prompt = prompt_template.format(username=event.get_sender_name())

            llm_response = await self.context.get_using_provider().text_chat(
                prompt=format_prompt,
                system_prompt=personality_prompt,
                contexts=contexts,
            )
            return llm_response.completion_text

        except Exception as e:
            logger.error(f"LLM 调用失败：{e}")
            return None


    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_poke(self, event: AiocqhttpMessageEvent):
        """监听并响应戳一戳事件"""
        raw_message = getattr(event.message_obj, "raw_message", None)

        if (
            not raw_message
            or not event.message_obj.message
            or not isinstance(event.message_obj.message[0], Poke)
        ):
            return

        target_id: int = raw_message.get("target_id", 0)
        user_id: int = raw_message.get("user_id", 0)
        self_id: int = raw_message.get("self_id", 0)
        group_id: int = raw_message.get("group_id", 0)

        # 冷却机制
        current_time = time.monotonic()
        last_time = self.last_trigger_time.get(user_id, 0)
        if current_time - last_time < self.cooldown_seconds:
            return
        self.last_trigger_time[user_id] = current_time

        # 过滤与自身无关的戳
        if target_id != self_id:
            # 跟戳机制
            if (
                group_id
                and user_id != self_id
                and random.random() < self.follow_poke_th
            ):
                await event.bot.group_poke(group_id=int(group_id), user_id=target_id)
            return

        # 随机选择一个响应函数
        selected_handler = random.choices(
            population=self.response_handlers, weights=self.weights, k=1
        )[0]

        try:
            await selected_handler(event)
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
                await client.friend_poke(user_id=int(send_id))
                await asyncio.sleep(self.poke_interval)
        event.stop_event()

    async def llm_respond(self, event: AiocqhttpMessageEvent):
        """调用llm回复"""
        text = await self._get_llm_respond(event, self.llm_prompt_template)
        await event.send(MessageChain(chain=[Plain(text)]))  # type: ignore
        event.stop_event()

    async def face_respond(self, event: AiocqhttpMessageEvent):
        """回复emoji(QQ表情)"""
        face_id = random.choice(self.face_ids) if self.face_ids else 287
        faces_chain: list[Face] = [Face(id=face_id)] * random.randint(1, 3)
        await event.send(MessageChain(chain=faces_chain))  # type: ignore
        event.stop_event()

    async def gallery_respond(self, event: AiocqhttpMessageEvent):
        """调用图库进行回复"""
        if files := list(self.gallery_path.iterdir()):
            selected_file = str(random.choice(files))
            await event.send(MessageChain(chain=[Image(selected_file)]))  # type: ignore
            event.stop_event()

    async def ban_respond(self, event: AiocqhttpMessageEvent):
        """禁言"""
        try:
            await event.bot.set_group_ban(
                group_id=int(event.get_group_id()),
                user_id=int(event.get_sender_id()),
                duration=random.randint(*self.ban_time_range),
            )
            prompt_template = self.ban_prompt_template

        except Exception:
            prompt_template = self.ban_fail_prompt_template
        finally:
            text = await self._get_llm_respond(event, prompt_template)
            await event.send(MessageChain(chain=[Plain(text)]))  # type: ignore
            event.stop_event()

    async def meme_respond(self, event: AiocqhttpMessageEvent):
        """回复合成的meme"""
        await self._send_cmd(event, random.choice(self.meme_cmds))

    async def api_respond(self, event: AiocqhttpMessageEvent):
        "调用api"
        await self._send_cmd(event, random.choice(self.api_cmds))

    async def box_respond(self, event: AiocqhttpMessageEvent):
        """开盒"""
        await self._send_cmd(event, "盒")

    @filter.command("戳", alias={"戳我"})
    async def poke_handle(self, event: AiocqhttpMessageEvent):
        """戳@xxx / 戳我"""
        target_ids = [
            str(seg.qq)
            for seg in event.get_messages()
            if isinstance(seg, At) and str(seg.qq) != event.get_self_id()
        ]
        if "戳我" in event.message_str.split():
            target_ids.append(event.get_sender_id())
        if not target_ids:
            return
        parsed_msg = event.message_str.split()
        times = (
            int(parsed_msg[-1])
            if parsed_msg[-1].isdigit()
            else random.randint(1, self.poke_max_times)
        )
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
        event.stop_event()
