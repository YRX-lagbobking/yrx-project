"""
四层记忆 · 抽取层 · L4 用户画像智能体

职责：
    从老人的话里**结构化**抽取健康画像字段（ProfileParams），逐字段写入 Redis Hash。

为什么用结构化抽取（response_format=ProfileParams）而不是让模型自由输出：
    画像字段会被提示词模板、报告生成、家属通知等多处消费，
    字段名必须稳定（name/age/allergy…），否则下游只能做字符串匹配，极易出错。
    用 Pydantic 模型约束输出，模型只能"填表"，填不出的留空。

字段严格为健康域（契约第 16 节）：
    name, age, gender, disease_history, allergy,
    emergency_contact, emergency_phone, communication_preference, weaknesses

硬性约定：
    1. 只提取明确信息，不编造、不推测（医疗场景下"编造"比"缺失"危险得多）；
    2. 空值跳过，不覆盖已有画像 —— 例如老人这轮没说年龄，绝不能把 age 写成 0 覆盖旧值；
    3. 涉及衰老的表述要温和（"听力下降"而不是"耳聋"，"行动不便"而不是"瘸"）；
    4. 模型不可用 / 抽取失败时安静跳过，不抛异常。
"""
import json
import re

from pydantic import BaseModel, Field

from app.ai.agent.memory.save.profile_memory import ProfileMemory
from app.ai.model.my_model import MyModel
from app.core.logger import logger


class ProfileParams(BaseModel):
    """老人健康画像字段（全部给默认值，模型未提及的字段保持默认）"""

    name: str = Field(default="", description="老人姓名或称呼，未提及留空")
    age: int = Field(default=0, description="老人年龄（整数岁数），未提及填 0")
    gender: str = Field(default="", description="性别：男 / 女，未提及留空")
    disease_history: str = Field(
        default="",
        description="慢病史与既往病史，如高血压、2 型糖尿病、冠心病、脑梗后遗症；未提及留空",
    )
    allergy: str = Field(default="", description="过敏史，如青霉素过敏、海鲜过敏；未提及留空")
    emergency_contact: str = Field(
        default="", description="紧急联系人及其与老人的关系，如儿子张伟、邻居李阿姨；未提及留空"
    )
    emergency_phone: str = Field(default="", description="紧急联系电话号码，未提及留空")
    communication_preference: str = Field(
        default="",
        description="沟通偏好，如说话慢一点、声音大一点、用方言、喜欢简短回答；未提及留空",
    )
    weaknesses: str = Field(
        default="",
        description="身体机能上的不便之处，用温和表述，如听力下降、腿脚不便、记性变差、视力模糊；未提及留空",
    )


class ProfileAgent:
    """L4 用户画像智能体：结构化抽取老人画像并逐字段入库"""

    def __init__(self, profile_memory: ProfileMemory):
        # 画像记忆的存储对象（属性名与 exam_0914 保持一致）
        self.profile_memory = profile_memory
        # 画像抽取提示词
        self.prompt = self.get_prompt()
        # 模型与 agent 懒加载：构造期不建连
        self.model = None
        self.agent = None

    # ---------------- 提示词 ----------------
    def get_prompt(self) -> str:
        """画像抽取提示词：只提取明确信息、不编造、衰老表述要温和"""
        self.prompt = """
一、角色：
    你是独居老人健康管家系统的「用户画像抽取助手」。
    老人正在和健康管家聊天，你的任务是从老人说的话里，抽取结构化的健康画像信息，
    用于后续问询时"更懂这位老人"（称呼、慢病、过敏、紧急联系人、沟通方式）。

二、字段定义（共 9 个，严格按字段含义填写）：
    1. name：老人姓名或自称的称呼（如"张桂芬""张奶奶"）；
    2. age：年龄，整数（如 78），未提及填 0；
    3. gender：男 / 女；
    4. disease_history：慢病史、既往病史（如高血压、2 型糖尿病、冠心病、脑梗后遗症）；
    5. allergy：过敏史（如青霉素过敏、海鲜过敏、磺胺过敏）；
    6. emergency_contact：紧急联系人及关系（如儿子张伟、女儿、邻居李阿姨、社区医生）；
    7. emergency_phone：紧急联系电话；
    8. communication_preference：沟通偏好（如说话慢一点、声音大一点、用方言、别说太长）；
    9. weaknesses：身体机能上的不便（如听力下降、腿脚不便、记性变差、视力模糊）。

三、提取规则（必须严格遵守）：
    1. **只提取明确信息**：老人没说过的，一律留空（字符串填 ""，年龄填 0）。
    2. **不要编造、不要推测、不要补全**：例如老人只说"我血压高"，不能推断出"高血压 10 年"；
       老人只说"我儿子"，不能编出姓名和电话。
    3. 同一字段的多条信息用顿号连接（如"高血压、2 型糖尿病"）。
    4. 紧急联系人必须来自老人的明确表述，不要拿姓名去凑电话。
    5. **衰老相关的称呼要温和**：用"听力下降""视力模糊""腿脚不便""记性变差"，
       禁止使用"耳聋""瞎子""瘸子""老糊涂"等刺耳表述。
    6. 老人只是在寒暄、或者一句话里没有任何画像信息时，所有字段留空。
    7. 不要总结、不要解释、不要追问。

四、示例：
    输入：我叫张桂芬，今年 78 了，有高血压，对青霉素过敏，我儿子张伟电话是 13800000000，你说话慢点。
    抽取：name="张桂芬"，age=78，gender=""，disease_history="高血压"，allergy="青霉素过敏"，
         emergency_contact="儿子张伟"，emergency_phone="13800000000"，
         communication_preference="说话慢一点"，weaknesses=""

    输入：我今天头有点晕，不想吃饭。
    抽取：所有字段留空（没有画像信息）。
"""
        return self.prompt

    # ---------------- 模型懒加载 ----------------
    def _get_agent(self):
        """
        懒加载画像抽取 agent（带 response_format=ProfileParams 的结构化输出）。

        说明：不同版本的 create_agent 对 response_format 的支持略有差异，
        这里用 try/except 兜住"参数不支持"的情况，失败时退回无结构化 agent，
        再在 update() 里用 JSON 解析兜底，保证画像能力不因版本差异整体失效。
        """
        if self.agent is not None:
            return self.agent
        try:
            from langchain.agents import create_agent

            # 画像抽取要求字段名严格对齐，用主模型（理解力更强，避免抽错字段）
            self.model = MyModel.get_model()
            try:
                self.agent = create_agent(
                    model=self.model,
                    system_prompt=self.prompt,
                    tools=[],
                    debug=False,
                    response_format=ProfileParams,  # 结构化输出：模型只能"填表"
                )
            except Exception as inner:
                logger.warning(f"结构化输出不可用，退回文本抽取 + JSON 解析：{inner}")
                self.agent = create_agent(
                    model=self.model,
                    system_prompt=self.prompt,
                    tools=[],
                    debug=False,
                )
        except Exception as e:
            logger.error(f"画像模型不可用，本轮跳过画像更新：{e}")
            self.agent = None
        return self.agent

    # ---------------- 记忆更新 ----------------
    async def update(self, question: str) -> None:
        """从老人这句话里抽取画像字段并逐字段入库（空值跳过）"""
        text = "" if question is None else str(question).strip()
        if not text:
            return

        agent = self._get_agent()
        if agent is None:
            return

        try:
            from langchain_core.messages import HumanMessage

            rs = await agent.ainvoke({"messages": [HumanMessage(content=text)]})
            data = self._extract_params(rs)
        except Exception as e:
            logger.error(f"用户画像抽取失败（跳过本轮）：{e}")
            return

        if not data:
            logger.info("本轮没有抽取到画像信息，跳过画像写入")
            return

        # 逐字段写入：空值跳过（关键！避免用 0/"" 覆盖已有画像）
        saved = []
        for key, value in data.items():
            try:
                if self._is_empty(value):
                    continue
                await self.profile_memory.save(key, value)
                saved.append(key)
            except Exception as e:
                logger.error(f"画像字段 {key} 写入失败：{e}")
        logger.info(f"用户画像更新完成，写入字段：{saved if saved else '无'}")

    # ---------------- 内部工具 ----------------
    @staticmethod
    def _is_empty(value) -> bool:
        """判断画像字段是否为空（空字符串 / 0 / None / 纯空白 都算空）"""
        if value is None:
            return True
        if isinstance(value, (int, float)):
            return value == 0
        return not str(value).strip()

    @staticmethod
    def _extract_params(rs) -> dict:
        """从 agent 返回值里取出画像 dict（兼容结构化响应 / JSON 文本两种形态）"""
        try:
            # 形态 1：create_agent 的结构化输出 {"structured_response": ProfileParams}
            if isinstance(rs, dict):
                structured = rs.get("structured_response")
                if structured is not None:
                    if isinstance(structured, ProfileParams):
                        return structured.model_dump()
                    if isinstance(structured, dict):
                        return structured
                    dump = getattr(structured, "model_dump", None)
                    if callable(dump):
                        return dump()
            # 形态 2：结构化对象直接返回
            if isinstance(rs, ProfileParams):
                return rs.model_dump()
        except Exception as e:
            logger.error(f"画像结构化结果解析失败：{e}")
        # 形态 3：文本里夹 JSON（降级路径）
        return ProfileAgent._parse_json_text(ProfileAgent._extract_content(rs))

    @staticmethod
    def _extract_content(rs) -> str:
        """从 agent 返回值里取纯文本（兼容 dict / 消息对象两种返回形态）"""
        try:
            if isinstance(rs, dict):
                msgs = rs.get("messages") or []
                if msgs:
                    return str(getattr(msgs[-1], "content", msgs[-1]) or "").strip()
                return str(rs.get("output", "") or "").strip()
            return str(getattr(rs, "content", rs) or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _parse_json_text(text: str) -> dict:
        """
        从文本里解析出画像 JSON（降级路径）。

        只保留 ProfileParams 中定义过的字段，防止模型多吐字段污染 Redis Hash。
        """
        if not text:
            return {}
        # 去掉 markdown 代码块围栏
        cleaned = re.sub(r"```(?:json)?", "", text).strip().strip("`").strip()
        candidate = cleaned
        # 截取第一个 { 到最后一个 } 之间的内容
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            candidate = candidate[start: end + 1]
        try:
            data = json.loads(candidate)
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        # 兼容 pydantic v2（model_fields）与 v1（__fields__）；取不到时不做过滤
        fields = getattr(ProfileParams, "model_fields", None) or getattr(ProfileParams, "__fields__", None)
        if not fields:
            return data
        allowed = set(fields.keys())
        return {k: v for k, v in data.items() if k in allowed}


if __name__ == "__main__":
    import asyncio

    async def test():
        # 自测：模型不可用时应当安静跳过（不抛异常）
        profile_memory = ProfileMemory("self_test_u001")
        agent = ProfileAgent(profile_memory)
        await agent.update("我叫张桂芬，今年 78 了，有高血压，对青霉素过敏，我儿子张伟电话是 13800000000，你说话慢点")
        print("用户画像：")
        print(await profile_memory.query())
        # 校验字段名与契约一致
        print("画像字段：", list(ProfileParams.model_fields.keys()))

    asyncio.run(test())
