"""
记忆组装层 · 提示词组装（PromptBuilder）

职责：
    把四层记忆拼成一段中文 system 提示词，注入到 LangGraph 的输入消息序列最前面。

四段结构（与需求文档 5.13 的四层记忆一一对应）：
    【角色设定】     固定话术：独居老人健康管家，不做诊断，紧急引导 120
    【L1 窗口记忆】  最近 N 轮对话原文（保留原话，语气与细节最真实）
    【L2 摘要记忆】  本次会话的压缩摘要（跨窗口的历史，防止"聊过又忘"）
    【L3 长期记忆】  跨会话的稳定事实（慢病/过敏/家庭情况/偏好）
    【L4 用户画像】  结构化属性（姓名/年龄/慢病/过敏/紧急联系人/沟通偏好）

设计说明：
    1. 四层记忆并行查询（asyncio.gather + return_exceptions），一层慢/挂不影响其它层，
       整体耗时取决于最慢的一层而不是四层之和；
    2. 每层为空时明确写"（暂无）"，让模型知道"确实没有"，而不是以为提示词被截断了；
    3. 紧急兜底话术强制写进系统提示词，属于医疗安全底线，不依赖模型临场判断。
"""
import asyncio

from app.ai.agent.memory.save.long_memory import LongMemory
from app.ai.agent.memory.save.profile_memory import ProfileMemory
from app.ai.agent.memory.save.summary_memory import SummaryMemory
from app.ai.agent.memory.save.window_memory import WindowMemory
from app.core.config import settings
from app.core.logger import logger

# 角色设定（强制前缀，保证"语气温和 + 不做诊断 + 紧急引导 120"始终生效）
ROLE_PROMPT = (
    "你是独居老人的智能健康管家，语气温和、耐心、口语化，"
    "输出不做医疗诊断，紧急情况引导拨打 120。"
)


class PromptBuilder:
    """四层记忆 → 中文 system 提示词"""

    def __init__(self, session_id: str, user_id: str):
        self.session_id = str(session_id)
        self.user_id = str(user_id)
        # 四层记忆对象（全部轻量，构造期不访问存储）
        self.window_memory = WindowMemory(self.session_id)
        self.summary_memory = SummaryMemory(self.session_id)
        self.long_memory = LongMemory()
        self.profile_memory = ProfileMemory(self.user_id)

    # ---------------- 对外：组装 ----------------
    async def builder_prompt(self, user_id: str, question: str) -> str:
        """查询四层记忆并拼装成完整的中文 system 提示词"""
        target_user_id = str(user_id) if user_id else self.user_id
        # 四层并行查询；用 return_exceptions=True 确保不会因为某层异常而整体失败
        window_rs, summary_rs, long_rs, profile_rs = await asyncio.gather(
            self._safe(self.window_memory.query()),
            self._safe(self.summary_memory.query()),
            self._safe(self.long_memory.query(target_user_id, question)),
            self._safe(self.profile_memory.query()),
            return_exceptions=True,
        )

        # 各层结果统一成"可读文本"（异常/None 一律视作空）
        window_text = self._format_window(window_rs)
        summary_text = self._format_text(summary_rs)
        long_text = self._format_list(long_rs)
        profile_text = self._format_text(profile_rs)

        prompt = f"""【角色设定】
{ROLE_PROMPT}

【L1 最近对话（窗口记忆）】
{window_text}

【L2 本次会话摘要（摘要记忆）】
{summary_text}

【L3 长期记忆（跨会话的稳定事实）】
{long_text}

【L4 用户画像】
{profile_text}

【本轮用户问题】
{question}

【回答要求】
1. 只用上面提供的记忆与常识回答，不要编造检查结果、诊断结论或药品剂量；
2. 结合用户画像调整称呼与语速，老人听力或理解力不佳时用短句、口语化表达；
3. 涉及异常指标（如血压、心率、血氧）时，先安抚，再给可执行建议，必要时提醒联系家属；
4. 出现胸痛、呼吸困难、意识不清、跌倒等紧急情况，第一时间引导拨打 120；
5. 不要重复已经给过的相同建议，除非老人再次询问。"""
        return prompt

    # ---------------- 内部：并行查询包装 ----------------
    @staticmethod
    async def _safe(coro):
        """把单个记忆查询包成"失败返回空"的协程，避免一层异常拖垮整体"""
        try:
            return await coro
        except Exception as e:
            logger.error(f"记忆查询失败（该层按空处理）：{e}")
            return None

    # ---------------- 内部：格式化 ----------------
    @staticmethod
    def _format_text(value) -> str:
        """把字符串型记忆结果格式化；空值统一显示"（暂无）"""
        text = "" if value is None else str(value).strip()
        return text if text else "（暂无）"

    @staticmethod
    def _format_list(value) -> str:
        """把 list[str] 型记忆结果格式化（长期记忆检索 Top3）"""
        if not value:
            return "（暂无）"
        if isinstance(value, str):
            return value.strip() or "（暂无）"
        try:
            lines = [f"- {str(i).strip()}" for i in value if str(i).strip()]
        except Exception:
            return "（暂无）"
        return "\n".join(lines) if lines else "（暂无）"

    @staticmethod
    def _format_window(value) -> str:
        """
        把窗口记忆 [{"role","content"}] 格式化成对话文本。

        只保留最近 settings.window_memory_rounds 条（存储层已裁剪，这里再兜一次，
        防止换了存储实现后条数失控，导致提示词超长）。
        """
        if not value or not isinstance(value, list):
            return "（暂无）"
        try:
            records = list(value)[-int(settings.window_memory_rounds):]
        except Exception:
            records = list(value)
        lines = []
        for item in records:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", ""))
            content = str(item.get("content", "") or "").strip()
            if not content:
                continue
            # user → 老人；其余（ai/assistant）→ 管家
            speaker = "老人" if role in ("user", "human") else "管家"
            lines.append(f"{speaker}：{content}")
        return "\n".join(lines) if lines else "（暂无）"


if __name__ == "__main__":
    import asyncio

    async def test():
        # 自测：直接构造 PromptBuilder 并打印组装结果（各层存储不可用时自动降级为空）
        builder = PromptBuilder("self_test_s001", "self_test_u001")
        text = await builder.builder_prompt("self_test_u001", "我今天头晕，血压 150/95")
        print(text)

    asyncio.run(test())
