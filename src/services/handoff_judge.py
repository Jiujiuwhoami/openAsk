"""多维 Handoff 判定引擎。

替代原有的单一阈值逻辑（_check_handoff_needed），
综合评估检索质量、重复提问、情感倾向、问题复杂度、对话轮次五个维度，
加权判定是否需要转接人工客服。

使用方式：
    judge = HandoffJudge()
    decision = await judge.evaluate(
        query="怎么退货",
        sources=[...],
        history=[...],  # Message 列表
        project_config={"handoff_threshold": 0.5},
    )
    if decision.suggested:
        print(f"建议转接，原因: {decision.reasons}")
"""

import re
from typing import Dict, List, Optional, Tuple

from src.domain.models import SearchResult
from src.utils.logger import get_logger

logger = get_logger(__name__)


class HandoffDecision:
    """转接决策结果。"""

    def __init__(
        self,
        suggested: bool,
        score: float,
        reasons: List[str],
        dimensions: Dict[str, float],
    ):
        self._suggested = suggested
        self._score = score
        self._reasons = reasons
        self._dimensions = dimensions

    @property
    def suggested(self) -> bool:
        """是否建议转接。"""
        return self._suggested

    @property
    def score(self) -> float:
        """综合评分 (0.0 ~ 1.0)，越高越需要转接。"""
        return self._score

    @property
    def reasons(self) -> List[str]:
        """判定原因列表。"""
        return list(self._reasons)

    @property
    def dimensions(self) -> Dict[str, float]:
        """各维度得分。"""
        return dict(self._dimensions)

    def __repr__(self) -> str:
        return (
            f"HandoffDecision(suggested={self._suggested}, "
            f"score={self._score:.2f}, reasons={self._reasons})"
        )


# 负面情绪关键词（中文）
# 注意：只放明确表达负面情绪的词汇，不包含正常的业务操作词（如"退货""退款"）
_NEGATIVE_KEYWORDS_ZH = [
    "投诉", "差评", "赔偿", "假货", "欺诈",
    "骗子", "垃圾", "太差", "很差", "不好", "不行", "差劲",
    "生气", "愤怒", "失望", "无语", "恶心", "受不了",
    "客服呢", "找经理", "投诉你",
    "怎么搞的", "什么情况", "怎么回事", "还没解决",
    "三番两次", "一直不", "每次都", "从来不",
]

# 负面情绪关键词（英文）
_NEGATIVE_KEYWORDS_EN = [
    "complaint", "refund", "return", "fake", "scam", "fraud",
    "angry", "upset", "disappointed", "terrible", "horrible",
    "useless", "worst", "awful", "never again",
    "human agent", "speak to human", "manager",
    "not working", "broken", "wrong",
]

# 复杂问题特征词
_COMPLEXITY_INDICATORS = [
    "如何", "怎样", "怎么", "为什么", "什么原因",
    "区别", "对比", "比较", "vs", "versus",
    "步骤", "流程", "方法", "方式",
    "how", "why", "what", "difference", "compare",
    "steps", "procedure", "process", "method",
    "if", "should", "which", "or",
]

# 简短问题（可能信息不足）
_SHORT_QUERY_THRESHOLD = 5
# 问候语（不计为复杂问题）
_GREETINGS = {"你好", "您好", "hello", "hi", "hey", "在吗", "在不在", "有人吗"}


class HandoffJudge:
    """多维转接判定引擎。

    权重配置（可项目级覆盖）：
    - retrieval_score_weight: 0.30  — 检索质量
    - repetition_weight: 0.20       — 重复提问
    - sentiment_weight: 0.20        — 情感分析
    - complexity_weight: 0.15       — 问题复杂度
    - round_weight: 0.15            — 对话轮次

    阈值：
    - handoff_threshold: 0.50       — 综合评分超过此值建议转接
    """

    DEFAULT_WEIGHTS = {
        "retrieval_score": 0.30,
        "repetition": 0.20,
        "sentiment": 0.20,
        "complexity": 0.15,
        "rounds": 0.15,
    }
    DEFAULT_THRESHOLD = 0.50
    # 超过此轮次开始加分
    ROUND_ESCALATION_START = 6
    # 超过此轮次直接建议转接
    ROUND_HARD_LIMIT = 15

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        threshold: float = DEFAULT_THRESHOLD,
    ):
        self._weights = weights or dict(self.DEFAULT_WEIGHTS)
        self._threshold = threshold
        logger.info(
            f"HandoffJudge 初始化, 权重={self._weights}, 阈值={self._threshold}"
        )

    def evaluate(
        self,
        query: str,
        sources: List[SearchResult],
        history: Optional[List] = None,
        project_config: Optional[Dict] = None,
    ) -> HandoffDecision:
        """综合评估是否需要转接人工客服。

        Args:
            query: 用户当前查询
            sources: 检索结果列表
            history: 会话历史消息列表（Message 对象）
            project_config: 项目级配置，可覆盖权重和阈值

        Returns:
            HandoffDecision: 决策结果
        """
        # 使用项目级配置覆盖（如果有）
        weights = self._weights
        threshold = self._threshold
        if project_config:
            weights = {
                k: project_config.get(f"handoff_weight_{k}", v)
                for k, v in weights.items()
            }
            threshold = project_config.get("handoff_threshold", threshold)

        history = history or []

        # 各维度独立评分
        dim_retrieval, reason_retrieval = self._evaluate_retrieval(sources)
        dim_repetition, reason_repetition = self._evaluate_repetition(query, history)
        dim_sentiment, reason_sentiment = self._evaluate_sentiment(query)
        dim_complexity, reason_complexity = self._evaluate_complexity(query)
        dim_rounds, reason_rounds = self._evaluate_rounds(history)

        dimensions = {
            "retrieval_score": dim_retrieval,
            "repetition": dim_repetition,
            "sentiment": dim_sentiment,
            "complexity": dim_complexity,
            "rounds": dim_rounds,
        }

        # 加权综合评分
        total_weight = sum(weights.get(k, 0) for k in dimensions)
        if total_weight <= 0:
            total_weight = 1.0  # 防止除零

        score = sum(
            dimensions[k] * weights.get(k, 0) for k in dimensions
        ) / total_weight

        # 硬限检查：任一维度满分（1.0）时，直接建议转接
        # 覆盖场景：无来源/检索全失败、严重负面情绪、轮次硬限、完全重复
        if any(v >= 1.0 for v in dimensions.values()):
            score = 1.0

        suggested = score >= threshold

        # 收集原因
        reasons = []
        if reason_retrieval:
            reasons.append(reason_retrieval)
        if reason_repetition:
            reasons.append(reason_repetition)
        if reason_sentiment:
            reasons.append(reason_sentiment)
        if reason_complexity:
            reasons.append(reason_complexity)
        if reason_rounds:
            reasons.append(reason_rounds)

        logger.debug(
            f"Handoff 评估: query='{query[:50]}', score={score:.2f}, "
            f"suggested={suggested}, dims={dimensions}"
        )

        return HandoffDecision(
            suggested=suggested,
            score=round(score, 4),
            reasons=reasons,
            dimensions=dimensions,
        )

    # ================================================================
    # 维度：检索质量 (30%)
    # ================================================================

    def _evaluate_retrieval(
        self, sources: List[SearchResult]
    ) -> Tuple[float, str]:
        """评估检索质量。

        评分规则：
        - 无来源 → 1.0 (需要转接)
        - 最高分 < 0.2 → 1.0
        - 最高分 0.2~0.35 → 0.8
        - 最高分 0.35~0.5 → 0.5
        - 最高分 0.5~0.7 → 0.2
        - 最高分 > 0.7 → 0.0
        """
        if not sources:
            return 1.0, "知识库未检索到相关文档"

        max_score = max((s.score for s in sources if s.score is not None), default=0.0)

        if max_score < 0.2:
            return 1.0, f"检索相关度极低（最高分 {max_score:.2f}）"
        elif max_score < 0.35:
            return 0.8, f"检索相关度偏低（最高分 {max_score:.2f}）"
        elif max_score < 0.5:
            return 0.5, f"检索相关度一般（最高分 {max_score:.2f}）"
        elif max_score < 0.7:
            return 0.2, ""
        else:
            return 0.0, ""

    # ================================================================
    # 维度：重复提问 (20%)
    # ================================================================

    @staticmethod
    def _get_role(msg) -> str:
        """兼容 Message 对象和 dict 格式的消息。"""
        if hasattr(msg, "role"):
            return msg.role
        if isinstance(msg, dict):
            return msg.get("role", "")
        return ""

    @staticmethod
    def _get_content(msg) -> str:
        """兼容 Message 对象和 dict 格式的消息。"""
        if hasattr(msg, "content"):
            return msg.content
        if isinstance(msg, dict):
            return msg.get("content", "")
        return ""

    def _evaluate_repetition(
        self, query: str, history: List
    ) -> Tuple[float, str]:
        """评估用户是否在重复提问。

        策略：
        1. 提取历史中所有用户消息
        2. 与当前查询做文本相似度比较
        3. 连续相似度高的判定为重复

        使用简单 Jaccard 相似度（无需嵌入模型）。
        """
        user_queries = [
            self._get_content(msg) for msg in history
            if self._get_role(msg) == "user"
        ]

        if len(user_queries) < 2:
            return 0.0, ""

        query_words = self._tokenize(query)
        if not query_words:
            return 0.0, ""

        # 从最近到最早，检查连续重复
        repeat_count = 0
        for prev_query in reversed(user_queries[-5:]):  # 最多检查最近 5 条
            prev_words = self._tokenize(prev_query)
            if not prev_words:
                continue
            similarity = self._jaccard_similarity(query_words, prev_words)
            if similarity > 0.85:
                repeat_count += 1
            else:
                break  # 一旦遇到不相似的，停止计数

        if repeat_count >= 3:
            return 1.0, f"连续 {repeat_count} 次提问内容高度重复"
        elif repeat_count == 2:
            return 0.7, f"连续 {repeat_count} 次提问内容重复"
        elif repeat_count == 1:
            return 0.3, "与上一个问题内容重复"
        else:
            return 0.0, ""

    @staticmethod
    def _tokenize(text: str) -> set:
        """简单分词：提取中英文单词和数字。"""
        text = text.lower().strip()
        if not text:
            return set()
        # 提取中文词组
        chinese = re.findall(r'[一-鿿]+', text)
        # 提取英文单词
        english = re.findall(r'[a-z]+', text)
        # 提取数字
        digits = re.findall(r'\d+', text)
        return set(chinese + english + digits)

    @staticmethod
    def _jaccard_similarity(a: set, b: set) -> float:
        """计算 Jaccard 相似度。"""
        if not a or not b:
            return 0.0
        intersection = a & b
        union = a | b
        return len(intersection) / len(union)

    # ================================================================
    # 维度：情感分析 (20%)
    # ================================================================

    def _evaluate_sentiment(self, query: str) -> Tuple[float, str]:
        """轻量情感分析（基于关键词 + 启发式特征）。

        无需外部 NLP 依赖，通过负面关键词、标点、语气词判断。
        同时支持中文和英文。
        """
        if not query.strip():
            return 0.0, ""

        query_lower = query.lower().strip()
        reasons = []

        # 1. 负面关键词匹配
        neg_words_zh = [w for w in _NEGATIVE_KEYWORDS_ZH if w in query]
        neg_words_en = [w for w in _NEGATIVE_KEYWORDS_EN if w in query_lower]
        neg_count = len(neg_words_zh) + len(neg_words_en)

        neg_score = 0.0
        if neg_count > 0:
            neg_score = min(0.4 + (neg_count - 1) * 0.15, 1.0)
            reasons.append(f"检测到负面关键词: {neg_count} 个")

        # 2. 感叹号/问号激增
        excl_mark = query.count("!") + query.count("！")
        qst_mark = query.count("?") + query.count("？")

        punct_score = 0.0
        if excl_mark >= 3:
            punct_score = 0.3
            reasons.append("使用多个感叹号，情绪可能激动")
        elif excl_mark == 2:
            punct_score = 0.15

        if qst_mark >= 4:
            punct_score = max(punct_score, 0.25)
            reasons.append("连续多个问号，可能迫切")

        # 3. 全大写单词（英文语境）
        all_caps = len(re.findall(r'\b[A-Z]{3,}\b', query))
        caps_score = min(all_caps * 0.15, 0.3)

        # 综合
        total_score = min(neg_score + punct_score + caps_score, 1.0)
        if total_score >= 0.5:
            return total_score, "；".join(reasons) if reasons else "检测到负面情绪"
        elif total_score >= 0.2:
            return total_score, ""
        else:
            return 0.0, ""

    # ================================================================
    # 维度：问题复杂度 (15%)
    # ================================================================

    def _evaluate_complexity(self, query: str) -> Tuple[float, str]:
        """评估问题复杂度。

        特征：
        - 长度：短问题可能信息不足，长问题可能复杂
        - 复杂指示词
        - 实体数量（数字、价格等）
        """
        if not query.strip():
            return 0.5, "空查询"

        # 问候语不计为复杂
        if query.strip().lower() in _GREETINGS:
            return 0.0, ""

        length = len(query)
        has_complex = any(w in query for w in _COMPLEXITY_INDICATORS)

        # 长度评分
        if length < _SHORT_QUERY_THRESHOLD:
            # 极短问题，信息不足
            length_score = 0.4
        elif length > 100:
            # 长问题，可能涉及多个方面
            length_score = 0.4
        elif length > 50:
            length_score = 0.2
        else:
            length_score = 0.0

        # 复杂指示词
        complexity_score = 0.3 if has_complex else 0.0

        # 实体密度（数字、金额、日期等）
        entities = set()
        # 金额: ¥100, $50, 100元
        money = re.findall(r'[¥$￥]?\d+[元美元]?', query)
        entities.update(money)
        # 日期格式
        dates = re.findall(r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?', query)
        entities.update(dates)
        # 手机号
        phones = re.findall(r'1[3-9]\d{9}', query)
        entities.update(phones)
        # 订单号（字母数字组合，6位以上）
        orders = re.findall(r'\b[A-Za-z0-9]{6,}\b', query)
        entities.update(orders)

        entity_score = min(len(entities) * 0.15, 0.5)

        total = max(length_score, complexity_score, entity_score)
        if total >= 0.3:
            return total, "问题涉及多个信息维度，可能较为复杂"
        return 0.0, ""

    # ================================================================
    # 维度：对话轮次 (15%)
    # ================================================================

    def _evaluate_rounds(self, history: List) -> Tuple[float, str]:
        """评估对话轮次。

        轮次 = 用户消息数（或消息数/2）。
        - 超过 ROUND_ESCALATION_START 轮开始加分
        - 超过 ROUND_HARD_LIMIT 轮直接建议转接（score = 1.0）
        """
        if not history:
            return 0.0, ""

        # 计算轮次：用户消息数
        user_count = sum(
            1 for msg in history if self._get_role(msg) == "user"
        )
        # 或粗略按消息数/2
        round_count = max(user_count, len(history) // 2)

        if round_count >= self.ROUND_HARD_LIMIT:
            return 1.0, f"对话已达 {round_count} 轮仍未解决，建议转人工"
        elif round_count >= self.ROUND_ESCALATION_START:
            # 超过起始轮次后，每多一轮加 0.15
            extra = round_count - self.ROUND_ESCALATION_START
            score = min(0.3 + extra * 0.15, 0.9)
            return score, f"对话已进行 {round_count} 轮"
        else:
            return 0.0, ""