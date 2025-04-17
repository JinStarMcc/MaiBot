from .observation import Observation, ChattingObservation
import asyncio
from src.plugins.moods.moods import MoodManager
from src.plugins.models.utils_model import LLMRequest
from src.config.config import global_config
import time
from typing import Optional
from datetime import datetime
import traceback
from src.plugins.chat.message import UserInfo
from src.plugins.chat.utils import parse_text_timestamps

# from src.plugins.schedule.schedule_generator import bot_schedule
# from src.plugins.memory_system.Hippocampus import HippocampusManager
from src.common.logger import get_module_logger, LogConfig, SUB_HEARTFLOW_STYLE_CONFIG  # noqa: E402

# from src.plugins.chat.utils import get_embedding
# from src.common.database import db
# from typing import Union
from src.individuality.individuality import Individuality
import random
from src.plugins.chat.chat_stream import ChatStream
from src.plugins.person_info.relationship_manager import relationship_manager
from src.plugins.chat.utils import get_recent_group_speaker
from ..plugins.utils.prompt_builder import Prompt, global_prompt_manager

subheartflow_config = LogConfig(
    # 使用海马体专用样式
    console_format=SUB_HEARTFLOW_STYLE_CONFIG["console_format"],
    file_format=SUB_HEARTFLOW_STYLE_CONFIG["file_format"],
)
logger = get_module_logger("subheartflow", config=subheartflow_config)


def init_prompt():
    prompt = ""
    # prompt += f"麦麦的总体想法是：{self.main_heartflow_info}\n\n"
    prompt += "{extra_info}\n"
    # prompt += "{prompt_schedule}\n"
    # prompt += "{relation_prompt_all}\n"
    prompt += "{prompt_personality}\n"
    prompt += "刚刚你的想法是{current_thinking_info}。可以适当转换话题\n"
    prompt += "-----------------------------------\n"
    prompt += "现在是{time_now}，你正在上网，和qq群里的网友们聊天，群里正在聊的话题是：\n{chat_observe_info}\n"
    prompt += "你现在{mood_info}\n"
    # prompt += "你注意到{sender_name}刚刚说：{message_txt}\n"
    prompt += "现在你接下去继续思考，产生新的想法，不要分点输出，输出连贯的内心独白"
    prompt += "思考时可以想想如何对群聊内容进行回复。回复的要求是：平淡一些，简短一些，说中文，尽量不要说你说过的话。如果你要回复，最好只回复一个人的一个话题\n"
    prompt += "请注意不要输出多余内容(包括前后缀，冒号和引号，括号， 表情，等)，不要带有括号和动作描写"
    prompt += "记得结合上述的消息，生成内心想法，文字不要浮夸，注意{bot_name}指的就是你。"
    Prompt(prompt, "sub_heartflow_prompt_before")
    prompt = ""
    # prompt += f"你现在正在做的事情是：{schedule_info}\n"
    prompt += "{extra_info}\n"
    prompt += "{prompt_personality}\n"
    prompt += "现在是{time_now}，你正在上网，和qq群里的网友们聊天，群里正在聊的话题是：\n{chat_observe_info}\n"
    prompt += "刚刚你的想法是{current_thinking_info}。"
    prompt += "你现在看到了网友们发的新消息:{message_new_info}\n"
    prompt += "你刚刚回复了群友们:{reply_info}"
    prompt += "你现在{mood_info}"
    prompt += "现在你接下去继续思考，产生新的想法，记得保留你刚刚的想法，不要分点输出，输出连贯的内心独白"
    prompt += "不要太长，但是记得结合上述的消息，要记得你的人设，关注聊天和新内容，关注你回复的内容，不要思考太多:"
    Prompt(prompt, "sub_heartflow_prompt_after")
    
        # prompt += f"你现在正在做的事情是：{schedule_info}\n"
    prompt += "{extra_info}\n"
    prompt += "{prompt_personality}\n"
    prompt += "现在是{time_now}，你正在上网，和qq群里的网友们聊天，群里正在聊的话题是：\n{chat_observe_info}\n"
    prompt += "刚刚你的想法是{current_thinking_info}。"
    prompt += "你现在看到了网友们发的新消息:{message_new_info}\n"
    # prompt += "你刚刚回复了群友们:{reply_info}"
    prompt += "你现在{mood_info}"
    prompt += "现在你接下去继续思考，产生新的想法，记得保留你刚刚的想法，不要分点输出，输出连贯的内心独白"
    prompt += "不要思考太多，不要输出多余内容(包括前后缀，冒号和引号，括号， 表情，等)，不要带有括号和动作描写"
    prompt += "记得结合上述的消息，生成内心想法，文字不要浮夸，注意{bot_name}指的就是你。"
    Prompt(prompt, "sub_heartflow_prompt_after_observe")


class CurrentState:
    def __init__(self):
        self.willing = 0
        self.current_state_info = ""

        self.mood_manager = MoodManager()
        self.mood = self.mood_manager.get_prompt()

    def update_current_state_info(self):
        self.current_state_info = self.mood_manager.get_current_mood()


class SubHeartflow:
    def __init__(self, subheartflow_id):
        self.subheartflow_id = subheartflow_id

        self.current_mind = ""
        self.past_mind = []
        self.current_state: CurrentState = CurrentState()
        self.llm_model = LLMRequest(
            model=global_config.llm_sub_heartflow,
            temperature=global_config.llm_sub_heartflow["temp"],
            max_tokens=600,
            request_type="sub_heart_flow",
        )

        self.main_heartflow_info = ""

        self.last_reply_time = time.time()
        self.last_active_time = time.time()  # 添加最后激活时间

        if not self.current_mind:
            self.current_mind = "你什么也没想"

        self.is_active = False

        self.observations: list[ChattingObservation] = []

        self.running_knowledges = []

        self._thinking_lock = asyncio.Lock() # 添加思考锁，防止并发思考

        self.bot_name = global_config.BOT_NICKNAME

    def add_observation(self, observation: Observation):
        """添加一个新的observation对象到列表中，如果已存在相同id的observation则不添加"""
        # 查找是否存在相同id的observation
        for existing_obs in self.observations:
            if existing_obs.observe_id == observation.observe_id:
                # 如果找到相同id的observation，直接返回
                return
        # 如果没有找到相同id的observation，则添加新的
        self.observations.append(observation)

    def remove_observation(self, observation: Observation):
        """从列表中移除一个observation对象"""
        if observation in self.observations:
            self.observations.remove(observation)

    def get_all_observations(self) -> list[Observation]:
        """获取所有observation对象"""
        return self.observations

    def clear_observations(self):
        """清空所有observation对象"""
        self.observations.clear()

    def _get_primary_observation(self) -> Optional[ChattingObservation]:
        """获取主要的（通常是第一个）ChattingObservation实例"""
        if self.observations and isinstance(self.observations[0], ChattingObservation):
            return self.observations[0]
        logger.warning(f"SubHeartflow {self.subheartflow_id} 没有找到有效的 ChattingObservation")
        return None

    async def subheartflow_start_working(self):
        while True:
            current_time = time.time()
            # --- 调整后台任务逻辑 --- #
            # 这个后台循环现在主要负责检查是否需要自我销毁
            # 不再主动进行思考或状态更新，这些由 HeartFC_Chat 驱动

            # 检查是否需要冻结（这个逻辑可能需要重新审视，因为激活状态现在由外部驱动）
            # if current_time - self.last_reply_time > global_config.sub_heart_flow_freeze_time:
            #     self.is_active = False
            # else:
            #     self.is_active = True
            #     self.last_active_time = current_time # 由外部调用（如 thinking）更新

            # 检查是否超过指定时间没有激活 (例如，没有被调用进行思考)
            if current_time - self.last_active_time > global_config.sub_heart_flow_stop_time:  # 例如 5 分钟
                logger.info(f"子心流 {self.subheartflow_id} 超过 {global_config.sub_heart_flow_stop_time} 秒没有激活，正在销毁..."
                            f" (Last active: {datetime.fromtimestamp(self.last_active_time).strftime('%Y-%m-%d %H:%M:%S')})")
                # 在这里添加实际的销毁逻辑，例如从主 Heartflow 管理器中移除自身
                # heartflow.remove_subheartflow(self.subheartflow_id) # 假设有这样的方法
                break  # 退出循环以停止任务

            # 不再需要内部驱动的状态更新和思考
            # self.current_state.update_current_state_info()
            # await self.do_a_thinking()
            # await self.judge_willing()

            await asyncio.sleep(global_config.sub_heart_flow_update_interval) # 定期检查销毁条件

    async def ensure_observed(self):
        """确保在思考前执行了观察"""
        observation = self._get_primary_observation()
        if observation:
            try:
                await observation.observe()
                logger.trace(f"[{self.subheartflow_id}] Observation updated before thinking.")
            except Exception as e:
                logger.error(f"[{self.subheartflow_id}] Error during pre-thinking observation: {e}")
                logger.error(traceback.format_exc())

    async def do_observe(self):
        # 现在推荐使用 ensure_observed()，但保留此方法以兼容旧用法（或特定场景）
        observation = self._get_primary_observation()
        if observation:
            await observation.observe()
        else:
            logger.error(f"[{self.subheartflow_id}] do_observe called but no valid observation found.")

    async def do_thinking_before_reply(
        self,  chat_stream: ChatStream, extra_info: str, obs_id: list[str] = None # 修改 obs_id 类型为 list[str]
    ):
        async with self._thinking_lock: # 获取思考锁
            # --- 在思考前确保观察已执行 --- #
            await self.ensure_observed()

            self.last_active_time = time.time() # 更新最后激活时间戳

            current_thinking_info = self.current_mind
            mood_info = self.current_state.mood
            observation = self._get_primary_observation()
            if not observation:
                logger.error(f"[{self.subheartflow_id}] Cannot perform thinking without observation.")
                return "", [] # 返回空结果

            # --- 获取观察信息 --- #
            chat_observe_info = ""
            if obs_id:
                try:
                    chat_observe_info = observation.get_observe_info(obs_id)
                    logger.debug(f"[{self.subheartflow_id}] Using specific observation IDs: {obs_id}")
                except Exception as e:
                    logger.error(f"[{self.subheartflow_id}] Error getting observe info with IDs {obs_id}: {e}. Falling back.")
                    chat_observe_info = observation.get_observe_info() # 出错时回退到默认观察
            else:
                chat_observe_info = observation.get_observe_info()
                logger.debug(f"[{self.subheartflow_id}] Using default observation info.")


            # --- 构建 Prompt (基本逻辑不变) --- #
            extra_info_prompt = ""
            if extra_info:
                for tool_name, tool_data in extra_info.items():
                    extra_info_prompt += f"{tool_name} 相关信息:\n"
                    for item in tool_data:
                        extra_info_prompt += f"- {item['name']}: {item['content']}\n"
            else:
                extra_info_prompt = "无工具信息。\n" # 提供默认值

            individuality = Individuality.get_instance()
            prompt_personality = f"你的名字是{self.bot_name},你"
            prompt_personality += individuality.personality.personality_core
            personality_sides = individuality.personality.personality_sides
            if personality_sides: random.shuffle(personality_sides); prompt_personality += f",{personality_sides[0]}"
            identity_detail = individuality.identity.identity_detail
            if identity_detail: random.shuffle(identity_detail); prompt_personality += f",{identity_detail[0]}"

            # who_chat_in_group = [
            #     (chat_stream.platform, sender_info.user_id, sender_info.user_nickname) # 先添加当前发送者
            # ]
            # # 获取最近发言者，排除当前发送者，避免重复
            # recent_speakers = get_recent_group_speaker(
            #     chat_stream.stream_id,
            #     (chat_stream.platform, sender_info.user_id),
            #     limit=global_config.MAX_CONTEXT_SIZE -1 # 减去当前发送者
            # )
            # who_chat_in_group.extend(recent_speakers)

            # relation_prompt = ""
            # unique_speakers = set() # 确保人物信息不重复
            # for person_tuple in who_chat_in_group:
            #     person_key = (person_tuple[0], person_tuple[1]) # 使用 platform+id 作为唯一标识
            #     if person_key not in unique_speakers:
            #         relation_prompt += await relationship_manager.build_relationship_info(person_tuple)
            #         unique_speakers.add(person_key)

            # relation_prompt_all = (await global_prompt_manager.get_prompt_async("relationship_prompt")).format(
            #     relation_prompt, sender_info.user_nickname
            # )

            # sender_name_sign = (
            #     f"<{chat_stream.platform}:{sender_info.user_id}:{sender_info.user_nickname}:{sender_info.user_cardname or 'NoCard'}>"
            # )

            time_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

            prompt = (await global_prompt_manager.get_prompt_async("sub_heartflow_prompt_before")).format(
                extra_info=extra_info_prompt,
                # relation_prompt_all=relation_prompt_all,
                prompt_personality=prompt_personality,
                current_thinking_info=current_thinking_info,
                time_now=time_now,
                chat_observe_info=chat_observe_info,
                mood_info=mood_info,
                # sender_name=sender_name_sign,
                # message_txt=message_txt,
                bot_name=self.bot_name,
            )

            prompt = await relationship_manager.convert_all_person_sign_to_person_name(prompt)
            prompt = parse_text_timestamps(prompt, mode="lite")

            logger.debug(f"[{self.subheartflow_id}] Thinking Prompt:\n{prompt}")

            try:
                response, reasoning_content = await self.llm_model.generate_response_async(prompt)
                if not response: # 如果 LLM 返回空，给一个默认想法
                    response = "(不知道该想些什么...)"
                    logger.warning(f"[{self.subheartflow_id}] LLM returned empty response for thinking.")
            except Exception as e:
                logger.error(f"[{self.subheartflow_id}] 内心独白获取失败: {e}")
                response = "(思考时发生错误...)" # 错误时的默认想法

            self.update_current_mind(response)

            # self.current_mind 已经在 update_current_mind 中更新

            logger.info(f"[{self.subheartflow_id}] 思考前脑内状态：{self.current_mind}")
            return self.current_mind, self.past_mind

    async def do_thinking_after_observe(
        self, message_txt: str, sender_info: UserInfo, chat_stream: ChatStream, extra_info: str, obs_id: int = None
    ):
        current_thinking_info = self.current_mind
        mood_info = self.current_state.mood
        # mood_info = "你很生气，很愤怒"
        observation = self.observations[0]
        if obs_id:
            print(f"11111111111有id,开始获取观察信息{obs_id}")
            chat_observe_info = observation.get_observe_info(obs_id)
        else:
            chat_observe_info = observation.get_observe_info()

        extra_info_prompt = ""
        for tool_name, tool_data in extra_info.items():
            extra_info_prompt += f"{tool_name} 相关信息:\n"
            for item in tool_data:
                extra_info_prompt += f"- {item['name']}: {item['content']}\n"

        # 开始构建prompt
        prompt_personality = f"你的名字是{self.bot_name},你"
        # person
        individuality = Individuality.get_instance()

        personality_core = individuality.personality.personality_core
        prompt_personality += personality_core

        personality_sides = individuality.personality.personality_sides
        random.shuffle(personality_sides)
        prompt_personality += f",{personality_sides[0]}"

        identity_detail = individuality.identity.identity_detail
        random.shuffle(identity_detail)
        prompt_personality += f",{identity_detail[0]}"

        # 关系
        who_chat_in_group = [
            (chat_stream.user_info.platform, chat_stream.user_info.user_id, chat_stream.user_info.user_nickname)
        ]
        who_chat_in_group += get_recent_group_speaker(
            chat_stream.stream_id,
            (chat_stream.user_info.platform, chat_stream.user_info.user_id),
            limit=global_config.MAX_CONTEXT_SIZE,
        )

        relation_prompt = ""
        for person in who_chat_in_group:
            relation_prompt += await relationship_manager.build_relationship_info(person)

        # relation_prompt_all = (
        #     f"{relation_prompt}关系等级越大，关系越好，请分析聊天记录，"
        #     f"根据你和说话者{sender_name}的关系和态度进行回复，明确你的立场和情感。"
        # )
        relation_prompt_all = (await global_prompt_manager.get_prompt_async("relationship_prompt")).format(
            relation_prompt, sender_info.user_nickname
        )

        sender_name_sign = (
            f"<{chat_stream.platform}:{sender_info.user_id}:{sender_info.user_nickname}:{sender_info.user_cardname}>"
        )

        time_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        prompt = (await global_prompt_manager.get_prompt_async("sub_heartflow_prompt_after_observe")).format(
            extra_info_prompt,
            # prompt_schedule,
            relation_prompt_all,
            prompt_personality,
            current_thinking_info,
            time_now,
            chat_observe_info,
            mood_info,
            sender_name_sign,
            message_txt,
            self.bot_name,
        )

        prompt = await relationship_manager.convert_all_person_sign_to_person_name(prompt)
        prompt = parse_text_timestamps(prompt, mode="lite")

        try:
            response, reasoning_content = await self.llm_model.generate_response_async(prompt)
        except Exception as e:
            logger.error(f"回复前内心独白获取失败: {e}")
            response = ""
        self.update_current_mind(response)

        self.current_mind = response

        logger.info(f"prompt:\n{prompt}\n")
        logger.info(f"麦麦的思考前脑内状态：{self.current_mind}")
        return self.current_mind, self.past_mind

    # async def do_thinking_after_reply(self, reply_content, chat_talking_prompt, extra_info):
    #     # print("麦麦回复之后脑袋转起来了")

    #     # 开始构建prompt
    #     prompt_personality = f"你的名字是{self.bot_name},你"
    #     # person
    #     individuality = Individuality.get_instance()

    #     personality_core = individuality.personality.personality_core
    #     prompt_personality += personality_core

    #     extra_info_prompt = ""
    #     for tool_name, tool_data in extra_info.items():
    #         extra_info_prompt += f"{tool_name} 相关信息:\n"
    #         for item in tool_data:
    #             extra_info_prompt += f"- {item['name']}: {item['content']}\n"

    #     personality_sides = individuality.personality.personality_sides
    #     random.shuffle(personality_sides)
    #     prompt_personality += f",{personality_sides[0]}"

    #     identity_detail = individuality.identity.identity_detail
    #     random.shuffle(identity_detail)
    #     prompt_personality += f",{identity_detail[0]}"

    #     current_thinking_info = self.current_mind
    #     mood_info = self.current_state.mood

    #     observation = self.observations[0]
    #     chat_observe_info = observation.observe_info

    #     message_new_info = chat_talking_prompt
    #     reply_info = reply_content

    #     time_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    #     prompt = (await global_prompt_manager.get_prompt_async("sub_heartflow_prompt_after")).format(
    #         extra_info_prompt,
    #         prompt_personality,
    #         time_now,
    #         chat_observe_info,
    #         current_thinking_info,
    #         message_new_info,
    #         reply_info,
    #         mood_info,
    #     )

    #     prompt = await relationship_manager.convert_all_person_sign_to_person_name(prompt)
    #     prompt = parse_text_timestamps(prompt, mode="lite")

    #     try:
    #         response, reasoning_content = await self.llm_model.generate_response_async(prompt)
    #     except Exception as e:
    #         logger.error(f"回复后内心独白获取失败: {e}")
    #         response = ""
    #     self.update_current_mind(response)

    #     self.current_mind = response
    #     logger.info(f"麦麦回复后的脑内状态：{self.current_mind}")

    #     self.last_reply_time = time.time()

    def update_current_mind(self, response):
        self.past_mind.append(self.current_mind)
        self.current_mind = response

    async def check_reply_trigger(self) -> bool:
        """根据观察到的信息和内部状态，判断是否应该触发一次回复。
           TODO: 实现具体的判断逻辑。
           例如：检查 self.observations[0].now_message_info 是否包含提及、问题，
           或者 self.current_mind 中是否包含强烈的回复意图等。
        """
        # Placeholder: 目前始终返回 False，需要后续实现
        logger.trace(f"[{self.subheartflow_id}] check_reply_trigger called. (Logic Pending)")
        # --- 实现触发逻辑 --- #
        # 示例：如果观察到的最新消息包含自己的名字，则有一定概率触发
        # observation = self._get_primary_observation()
        # if observation and self.bot_name in observation.now_message_info[-100:]: # 检查最后100个字符
        #     if random.random() < 0.3: # 30% 概率触发
        #         logger.info(f"[{self.subheartflow_id}] Triggering reply based on mention.")
        #         return True
        # ------------------ #
        return False # 默认不触发


init_prompt()
# subheartflow = SubHeartflow()
