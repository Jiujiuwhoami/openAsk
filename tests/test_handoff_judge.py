"""多维 Handoff 判定引擎测试。

覆盖：
- 各维度独立评分（检索/重复/情感/复杂度/轮次）
- 综合加权评分
- 阈值判定
- 项目配置覆盖
- 消息格式兼容（对象 + dict）
"""

import pytest

from src.domain.models import SearchResult
from src.services.handoff_judge import HandoffJudge, HandoffDecision


def make_source(score: float, content: str = "文档内容") -> SearchResult:
    return SearchResult(doc_id=f"doc_{score}", score=score, content=content, title="文档")


class TestRetrievalDimension:
    """检索质量维度。"""

    def test_no_sources(self):
        judge = HandoffJudge()
        d = judge.evaluate("怎么退货", [])
        assert d.dimensions["retrieval_score"] == 1.0
        assert d.reasons and any("未检索" in r for r in d.reasons)

    def test_high_score(self):
        judge = HandoffJudge()
        d = judge.evaluate("退货政策", [make_source(0.9), make_source(0.8)])
        assert d.dimensions["retrieval_score"] == 0.0

    def test_medium_score(self):
        judge = HandoffJudge()
        d = judge.evaluate("退货政策", [make_source(0.4)])
        assert d.dimensions["retrieval_score"] == 0.5

    def test_low_score(self):
        judge = HandoffJudge()
        d = judge.evaluate("退货政策", [make_source(0.19)])
        assert d.dimensions["retrieval_score"] == 1.0

    def test_very_low_score(self):
        judge = HandoffJudge()
        d = judge.evaluate("退货政策", [make_source(0.1)])
        assert d.dimensions["retrieval_score"] == 1.0

    def test_empty_query(self):
        judge = HandoffJudge()
        d = judge.evaluate("", [])
        # 空查询 + 无来源 → retrieval_score=1.0 → 硬限触发
        assert d.suggested is True
        assert d.score == 1.0


class TestRepetitionDimension:
    """重复提问维度。"""

    def _history(self, queries):
        return [{"role": "user", "content": q} for q in queries]

    def test_no_history(self):
        judge = HandoffJudge()
        d = judge.evaluate("怎么退款", [], history=[])
        assert d.dimensions["repetition"] == 0.0

    def test_single_history(self):
        judge = HandoffJudge()
        d = judge.evaluate("怎么退款", [], history=self._history(["怎么退款"]))
        assert d.dimensions["repetition"] == 0.0  # 只有一条历史，不足以判断重复

    def test_repeated_queries(self):
        judge = HandoffJudge()
        history = self._history(["怎么退款", "怎么退款", "怎么退款"])
        d = judge.evaluate("怎么退款", [], history=history)
        assert d.dimensions["repetition"] >= 0.7
        assert any("重复" in r for r in d.reasons)

    def test_different_queries(self):
        judge = HandoffJudge()
        history = self._history(["怎么退货", "物流在哪查", "开发票"]
                                )
        d = judge.evaluate("怎么退款", [], history=history)
        assert d.dimensions["repetition"] == 0.0

    def test_same_question_twice(self):
        judge = HandoffJudge()
        history = self._history(["怎么退款", "怎么退款"])
        d = judge.evaluate("怎么退款", [], history=history)
        # 2 条历史 + 当前查询 = 3 条相同 → repeat_count = 2 → 0.7
        assert d.dimensions["repetition"] == 0.7


class TestSentimentDimension:
    """情感分析维度。"""

    def test_negative_keyword(self):
        judge = HandoffJudge()
        d = judge.evaluate("你们是骗子！我要投诉！", [make_source(0.9)])
        assert d.dimensions["sentiment"] >= 0.4

    def test_multiple_negative_keywords(self):
        judge = HandoffJudge()
        d = judge.evaluate("骗子！垃圾！投诉！差评！", [make_source(0.9)])
        assert d.dimensions["sentiment"] >= 0.5

    def test_exclamation_marks(self):
        judge = HandoffJudge()
        d = judge.evaluate("怎么还没发货！！！", [make_source(0.9)])
        assert d.dimensions["sentiment"] >= 0.2

    def test_neutral_query(self):
        judge = HandoffJudge()
        d = judge.evaluate("请问退货政策是什么", [make_source(0.9)])
        assert d.dimensions["sentiment"] == 0.0

    def test_english_negative(self):
        judge = HandoffJudge()
        d = judge.evaluate("This is TERRIBLE and I want a refund", [make_source(0.9)])
        assert d.dimensions["sentiment"] >= 0.4


class TestComplexityDimension:
    """问题复杂度维度。"""

    def test_short_query(self):
        judge = HandoffJudge()
        d = judge.evaluate("怎么办", [make_source(0.9)])
        assert d.dimensions["complexity"] > 0

    def test_complex_indicators(self):
        judge = HandoffJudge()
        d = judge.evaluate("跨品牌退货和换货的流程有什么区别", [make_source(0.9)])
        assert d.dimensions["complexity"] > 0

    def test_entity_density(self):
        judge = HandoffJudge()
        d = judge.evaluate("订单 20230715001234 的退款到账了吗，金额 ¥298.5", [make_source(0.9)])
        assert d.dimensions["complexity"] > 0

    def test_simple_query(self):
        judge = HandoffJudge()
        d = judge.evaluate("你好", [make_source(0.9)])
        assert d.dimensions["complexity"] == 0.0


class TestRoundsDimension:
    """对话轮次维度。"""

    def _rounds(self, count):
        history = []
        for i in range(count):
            history.append({"role": "user", "content": f"问题{i}"})
            history.append({"role": "assistant", "content": f"回答{i}"})
        return history

    def test_few_rounds(self):
        judge = HandoffJudge()
        d = judge.evaluate("问题", [], history=self._rounds(2))
        assert d.dimensions["rounds"] == 0.0

    def test_escalation_rounds(self):
        judge = HandoffJudge()
        d = judge.evaluate("问题", [], history=self._rounds(8))
        assert d.dimensions["rounds"] >= 0.3

    def test_hard_limit_rounds(self):
        judge = HandoffJudge()
        d = judge.evaluate("问题", [], history=self._rounds(16))
        assert d.dimensions["rounds"] == 1.0
        assert d.suggested is True  # 硬限直接转接


class TestCombinedScoring:
    """综合评分与阈值。"""

    def test_high_quality_answer_no_handoff(self):
        """检索质量好、无负面情绪、无重复 → 不转接。"""
        judge = HandoffJudge()
        d = judge.evaluate(
            "退货政策是什么",
            [make_source(0.9), make_source(0.85)],
            history=[{"role": "user", "content": "你好"}, {"role": "assistant", "content": "您好"}],
        )
        assert d.suggested is False
        assert d.score < 0.5

    def test_low_quality_answer_handoff(self):
        """无来源 → 转接。"""
        judge = HandoffJudge()
        d = judge.evaluate("很偏门的问题", [], history=[])
        assert d.suggested is True
        assert d.score >= 0.5

    def test_negative_sentiment_with_good_retrieval(self):
        """检索好但情绪负面的问题 → 情绪维度拉高分数。"""
        judge = HandoffJudge()
        d = judge.evaluate(
            "你们是骗子，我要投诉退款！",
            [make_source(0.9)],
            history=[],
        )
        # 情感维度 20% 权重，0.4 以上就足以超过阈值
        assert d.dimensions["sentiment"] >= 0.4

    def test_combined_reasons(self):
        """多维度同时触发时，reasons 包含多个原因。"""
        judge = HandoffJudge()
        d = judge.evaluate(
            "怎么退款怎么退款怎么退款！！",
            [make_source(0.1)],
            history=[
                {"role": "user", "content": "怎么退款怎么退款怎么退款！！"},
                {"role": "assistant", "content": "请查看退货政策"},
            ],
        )
        assert len(d.reasons) >= 2
        assert d.suggested is True

    def test_project_config_threshold(self):
        """项目配置可覆盖阈值。"""
        judge = HandoffJudge(threshold=0.5)
        # 检索一般（0.5 分 * 0.3 权重）+ 无其他 → 分数 0.15，默认不转接
        d = judge.evaluate("普通问题", [make_source(0.4)], history=[])
        assert d.suggested is False

        # 项目配置降低阈值到 0.1 → 转接
        d2 = judge.evaluate(
            "普通问题", [make_source(0.4)], history=[], project_config={"handoff_threshold": 0.1}
        )
        assert d2.suggested is True

    def test_project_config_weights(self):
        """项目配置可覆盖权重。"""
        judge = HandoffJudge()
        # 覆盖权重：复杂度和情感权重为 0，只看检索
        d = judge.evaluate(
            "怎么退款！！", [make_source(0.9)],
            history=[],
            project_config={
                "handoff_weight_retrieval_score": 1.0,
                "handoff_weight_sentiment": 0.0,
                "handoff_weight_repetition": 0.0,
                "handoff_weight_complexity": 0.0,
                "handoff_weight_rounds": 0.0,
            },
        )
        assert d.suggested is False  # 检索好，不看情感

    def test_custom_weights_constructor(self):
        """构造函数支持自定义权重。"""
        judge = HandoffJudge(weights={"retrieval_score": 1.0}, threshold=0.5)
        d = judge.evaluate("问题", [make_source(0.9)], history=[])
        assert d.suggested is False

        d2 = judge.evaluate("问题", [make_source(0.1)], history=[])
        assert d2.suggested is True


class TestMessageFormatCompatibility:
    """消息格式兼容：Message 对象 + dict。"""

    def test_dict_messages(self):
        judge = HandoffJudge()
        history = [
            {"role": "user", "content": "怎么退款"},
            {"role": "assistant", "content": "请查看政策"},
            {"role": "user", "content": "怎么退款"},
        ]
        d = judge.evaluate("怎么退款", [], history=history)
        assert d.dimensions["repetition"] > 0

    def test_object_messages(self):
        judge = HandoffJudge()
        from src.domain.conversation import Message
        history = [
            Message(id=1, conversation_id="c1", role="user", content="怎么退款"),
            Message(id=2, conversation_id="c1", role="assistant", content="请查看政策"),
            Message(id=3, conversation_id="c1", role="user", content="怎么退款"),
        ]
        d = judge.evaluate("怎么退款", [], history=history)
        assert d.dimensions["repetition"] > 0

    def test_mixed_formats(self):
        judge = HandoffJudge()
        history = [
            {"role": "user", "content": "怎么退款"},
            {"role": "assistant", "content": "请查看政策"},
        ]
        d = judge.evaluate("怎么退款", [], history=history)
        assert d.dimensions["repetition"] == 0.0  # 只有一条 user，不足以判断


class TestDecisionObject:
    """HandoffDecision 对象行为。"""

    def test_decision_immutability(self):
        judge = HandoffJudge()
        d = judge.evaluate("问题", [make_source(0.1)])
        reasons = d.reasons
        reasons.append("test")
        assert "test" not in d.reasons  # 返回的是副本

        dims = d.dimensions
        dims["test"] = 1.0
        assert "test" not in d.dimensions

    def test_score_range(self):
        judge = HandoffJudge()
        d = judge.evaluate("问题", [make_source(0.9)])
        assert 0.0 <= d.score <= 1.0

    def test_repr(self):
        judge = HandoffJudge()
        d = judge.evaluate("问题", [make_source(0.9)])
        assert "HandoffDecision" in repr(d)