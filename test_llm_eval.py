import asyncio
from testagent.config.settings import get_settings
from testagent.llm.openai_provider import OpenAIProvider
from testagent.eval.graders.llm_rubric import LlmRubricGrader
from testagent.eval.models import GraderConfig, EvalTask, Transcript, TranscriptSummary

async def test():
    settings = get_settings()
    llm = OpenAIProvider(settings)

    grader = LlmRubricGrader(
        GraderConfig(grader_type='llm_rubric', rubric='''
评分标准（1-5分，>=4通过）：
- 搜索结果页面正确展示
- 搜索结果与关键词相关
- 结果列表完整可见
- 操作流程合理
'''),
        llm_provider=llm,
    )
    result = await grader.grade(
        Transcript(messages=[
            {"role": "user", "content": "打开B站搜索科技"},
            {"role": "assistant", "content": "我先截图看当前页面", "tool_calls": [{"function": {"name": "screenshot"}}]},
            {"role": "tool", "content": '{"result": "ok"}'},
            {"role": "assistant", "content": "看到首页了，点击搜索框", "tool_calls": [{"function": {"name": "tap"}}]},
            {"role": "tool", "content": '{"result": "ok"}'},
            {"role": "assistant", "content": "输入科技并搜索", "tool_calls": [{"function": {"name": "type_text"}}]},
            {"role": "tool", "content": '{"result": "ok"}'},
            {"role": "assistant", "content": "搜索完成，展示结果页面"},
        ], summary=TranscriptSummary(n_turns=4, n_tool_calls=3, tool_call_sequence=["screenshot", "tap", "type_text"])),
        EvalTask(id='search_test', description='B站搜索功能测试', instruction='打开B站搜索"科技"'),
    )
    print(f'Score: {result.score:.2f}, Passed: {result.passed}')
    print(f'Details: {result.details}')

asyncio.run(test())
