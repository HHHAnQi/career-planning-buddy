"""Rubric-scoring judge for plan candidates (docs/standards/plan-quality-rubric.md).

Two judges with the same output contract:

* ``DeterministicRubricJudge`` — scores what code can verify (OpenAI Evals
  practice: deterministic checks first): horizon compliance from date
  arithmetic, evidence grounding from reference visibility. Subjective
  dimensions (goal alignment, executability) are left ``None``.
* ``OpenAICompatibleRubricJudge`` — scores all four dimensions via the
  independent judge model (``judge_llm_*`` settings), one repair attempt,
  fail closed. The prompt embeds the same rubric text as the human
  annotation guide; changing the rubric bumps the prompt version and
  requires recalibration.

Calibration: ``scripts/calibrate_rubric_judge.py`` compares judge output
against the human-annotated worksheet (Cohen's kappa on good/bad bands,
Spearman on raw scores) and applies the gate from
``evals.v2.agreement``.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Literal, Protocol

import httpx
from pydantic import Field, ValidationError

from app.agent.errors import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.core.config import Settings
from app.schemas.agent_runs import PlanCandidate
from app.schemas.base import StrictModel

RUBRIC_JUDGE_PROMPT_VERSION = "rubric_judge_v4"

DIMENSIONS = (
    "goal_alignment",
    "evidence_grounding",
    "executability",
    "horizon_compliance",
)

RUBRIC_SYSTEM_PROMPT = """你是规划质量评审员，按以下四个维度为规划候选打分（每维 1-5 整数）。

D1 goal_alignment 目标对齐：规划是否回应用户请求与画像（方向、阶段、技能水平、时间预算）。
5=精确匹配并尊重时间预算；3=方向正确但阶段错位或忽略明确约束；1=答非所问或强加用户未提的目标。
补充判据：a)预算利用率——尊重预算不等于只不超限，单日任务合计应达到声明预算的80%以上，明显用不满（如预算75分钟只排45分钟）按3分处理；b)流程完整性——任务序列不得跳过必要环节（如开发项目从环境搭建直接跳到联调、缺失开发环节），关键环节缺失按3分以下处理。
D2 evidence_grounding 证据支撑：关键主张是否有证据目录中的来源支撑。
5=关键主张全部有可见证据且引用真实；3=多数有支撑但一处关键判断无证据，或证据目录为空、规划未引用任何证据（不可验证但无造假）；
1=仅当引用了证据目录中不存在的 id（硬错误）。注意：目录为空时规划没有引用，这是 3 分（不可验证），绝不是 1 分。
D3 executability 可执行性：任务具体、交付物可验证、单日工作量合理。
5=每个任务拿到就能开始做（步骤可操作）、交付物可独立检验、时长贴合预算；3=含糊占位≥2处（如"学习相关知识""了解行业"）或单日超预算50%；1=愿望式空洞任务或严重超载。
场景真实性：任务内容须符合目标用户的真实场景——本项目用户是中国求职者，沟通投递以招聘平台（Boss直聘等）为主，出现"撰写求职信"类脱离场景的设计按3分以下处理；任务量也要与目标周期匹配（如探索岗位需求一天只查3家公司，覆盖面不足）。
校准警告：评审常见错误是看到列表结构完整就给5分。先逐个任务问：用户明天打开这条任务，不看别的能否直接开始并知道"做完了"长什么样？任何一题答不上，该任务不合格；两个以上不合格只能给3分。冗长但无信息增量的堆砌同样按3分以下处理（反注水条款）。
D4 horizon_compliance 周期合规：7天任务与 weekly_focus 对齐、日期连续、周序不超前。
5=日期连续无缺且每个任务的主题与当周focus直接对应；3=日期连续但1-2个任务与当周focus脱节（做的是别的周或与focus无关的事）；1=日期缺失/重复或周对齐混乱。
判定方法：对每个任务问"这个任务做完，当周focus的success_signal是否更近了？"答不上就是脱节；只看任务内容，任务或理由里复述focus字眼不算对应。
校准警告：不要因为日期连续就给5分——日期连续只是不扣分的必要条件，focus对应才是本维核心；多数任务只是泛泛推进（如都是"学习/复习"类）而与具体focus无对应时，最高3分。

硬规则：
- 只依据输入中的请求、画像与证据目录评分，不引入外部知识。
- 不以输出长度论质量；禁止"都很好"式和稀泥。
- 逐维度输出 1-5 整数分与简短理由（每维不超过60字）。
只输出 JSON，键为 goal_alignment / evidence_grounding / executability /
horizon_compliance（各为 1-5 整数）与 rationales（每维一条简短理由）。"""

_CODE_FENCE = re.compile(r"(?s)^```(?:json)?\s*(.*?)\s*```$")


class RubricJudgeOutput(StrictModel):
    goal_alignment: int | None = Field(default=None, ge=1, le=5)
    evidence_grounding: int | None = Field(default=None, ge=1, le=5)
    executability: int | None = Field(default=None, ge=1, le=5)
    horizon_compliance: int | None = Field(default=None, ge=1, le=5)
    rationales: dict[str, str] = Field(default_factory=dict)

    def scored_dimensions(self) -> list[str]:
        return [name for name in DIMENSIONS if getattr(self, name) is not None]


class RubricJudgeInput(StrictModel):
    request_message: str
    profile_summary: str
    time_budget_minutes: int
    evidence_catalog_ids: list[str] = Field(default_factory=list)
    candidate: PlanCandidate


class RubricJudge(Protocol):
    prompt_version: str

    async def score(self, prompt: RubricJudgeInput) -> RubricJudgeOutput: ...


def parse_rubric_output(text: str) -> RubricJudgeOutput | None:
    """Parse JSON (optionally fenced); None when invalid."""

    stripped = text.strip()
    fenced = _CODE_FENCE.match(stripped)
    if fenced is not None:
        stripped = fenced.group(1)
    try:
        payload: object = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return RubricJudgeOutput.model_validate(payload)
    except ValidationError:
        return None


def build_rubric_user_message(prompt: RubricJudgeInput) -> str:
    catalog = (
        ", ".join(prompt.evidence_catalog_ids)
        if prompt.evidence_catalog_ids
        else "(空)"
    )
    return json.dumps(
        {
            "request": prompt.request_message,
            "profile": prompt.profile_summary,
            "time_budget_minutes": prompt.time_budget_minutes,
            "evidence_catalog_ids": catalog,
            "candidate": prompt.candidate.model_dump(mode="json"),
        },
        ensure_ascii=False,
        indent=2,
    )


class DeterministicRubricJudge:
    """Scores the two verifiable dimensions; subjective dims stay None."""

    prompt_version = RUBRIC_JUDGE_PROMPT_VERSION

    async def score(self, prompt: RubricJudgeInput) -> RubricJudgeOutput:
        candidate = prompt.candidate
        horizon_score, horizon_rationale = self._horizon_compliance(candidate)
        evidence_score, evidence_rationale = self._evidence_grounding(prompt)
        return RubricJudgeOutput(
            horizon_compliance=horizon_score,
            evidence_grounding=evidence_score,
            rationales={
                "horizon_compliance": horizon_rationale,
                "evidence_grounding": evidence_rationale,
            },
        )

    def _horizon_compliance(
        self, candidate: PlanCandidate
    ) -> tuple[int, str]:
        tasks = candidate.tasks
        dates = [task.scheduled_date for task in tasks]
        expected = [candidate.plan_date + timedelta(days=i) for i in range(len(tasks))]
        if dates != expected:
            return 1, "日期不连续或缺漏"
        focus_weeks = {item.week_index for item in candidate.weekly_focus}
        if not focus_weeks.issubset(set(range(1, 9))):
            return 1, "week_index 越界"
        return 5, "日期连续且周序合法"

    def _evidence_grounding(
        self, prompt: RubricJudgeInput
    ) -> tuple[int, str]:
        refs = prompt.candidate.evidence_refs
        catalog = set(prompt.evidence_catalog_ids)
        ref_ids = [str(item.id) for item in refs]
        forged = [item for item in ref_ids if item not in catalog]
        if forged:
            return 1, f"引用了目录外的证据 {len(forged)} 处（硬错误）"
        if not ref_ids:
            return 3, "无证据引用，主张不可验证"
        return 5, f"{len(ref_ids)} 处引用均在证据目录内"


class OpenAICompatibleRubricJudge:
    """Full four-dimension LLM scoring on the independent judge model."""

    prompt_version = RUBRIC_JUDGE_PROMPT_VERSION
    REPAIR_INSTRUCTION = (
        "上一输出无法解析为符合要求的 JSON。重新输出：四个维度的 1-5 整数分"
        "与 rationales，只输出 JSON 本体。"
    )

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 30,
        max_output_tokens: int = 800,
        transport: httpx.AsyncBaseTransport | None = None,
        disable_thinking: bool = False,
    ) -> None:
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._max_tokens = max_output_tokens
        self._transport = transport
        # DeepSeek's hybrid reasoning models burn the output budget on
        # hidden thinking and return empty content under json_object;
        # disabling thinking is required for stable structured judging.
        self._disable_thinking = disable_thinking

    async def score(self, prompt: RubricJudgeInput) -> RubricJudgeOutput:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": RUBRIC_SYSTEM_PROMPT},
            {"role": "user", "content": build_rubric_user_message(prompt)},
        ]
        body_text = await self._send(messages)
        output = parse_rubric_output(body_text)
        if output is None or not output.scored_dimensions():
            body_text = await self._send(
                messages
                + [
                    {"role": "assistant", "content": body_text},
                    {"role": "user", "content": self.REPAIR_INSTRUCTION},
                ]
            )
            output = parse_rubric_output(body_text)
        if output is None or not output.scored_dimensions():
            raise ProviderUnavailableError(
                "Rubric Judge returned an unparseable score", retryable=False
            )
        return output

    async def _send(self, messages: list[dict[str, str]]) -> str:
        request_body: dict[str, object] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self._max_tokens,
            "response_format": {"type": "json_object"},
        }
        if self._disable_thinking:
            request_body["thinking"] = {"type": "disabled"}
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            ) as client:
                response = await client.post(self._endpoint, json=request_body)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Rubric Judge timed out") from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError("Rubric Judge unreachable") from exc
        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError("Rubric Judge auth rejected")
        if response.status_code == 429:
            raise ProviderRateLimitError("Rubric Judge rate limited")
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                f"Rubric Judge HTTP {response.status_code}"
            )
        try:
            body: object = response.json()
        except ValueError as exc:
            raise ProviderUnavailableError("Rubric Judge invalid body") from exc
        choices = body.get("choices") if isinstance(body, dict) else None
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ProviderUnavailableError("Rubric Judge empty content")
        return content


def build_rubric_judge(
    settings: Settings, *, judge_mode: Literal["deterministic", "llm"] = "llm"
) -> RubricJudge:
    """Build the configured rubric judge; never silently substitute."""

    if judge_mode == "deterministic":
        return DeterministicRubricJudge()
    if (
        settings.judge_llm_api_key is None
        or settings.judge_llm_base_url is None
        or settings.judge_llm_model is None
    ):
        raise ProviderUnavailableError(
            "LLM rubric judge requires judge_llm_api_key/base_url/model",
            retryable=False,
        )
    return OpenAICompatibleRubricJudge(
        api_key=settings.judge_llm_api_key.get_secret_value(),
        base_url=str(settings.judge_llm_base_url),
        model=settings.judge_llm_model,
        timeout_seconds=settings.judge_llm_timeout_seconds,
        max_output_tokens=settings.judge_llm_max_output_tokens,
        disable_thinking="deepseek" in str(settings.judge_llm_base_url).lower(),
    )


__all__ = [
    "DIMENSIONS",
    "DeterministicRubricJudge",
    "OpenAICompatibleRubricJudge",
    "RUBRIC_JUDGE_PROMPT_VERSION",
    "RubricJudge",
    "RubricJudgeInput",
    "RubricJudgeOutput",
    "build_rubric_judge",
    "build_rubric_user_message",
    "parse_rubric_output",
]
