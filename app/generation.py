import logging

from anthropic import Anthropic
from pydantic import ValidationError
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_fixed

from app import config
from app.prompt_template import get_system_prompt, build_user_prompt
from app.schema import TrainingSessionPlan

_TOOL_NAME = "create_training_plan"


class PlanGenerator:
    def __init__(self, client: Anthropic) -> None:
        self.client = client
        self._log = logging.getLogger(__name__)
        self._system_prompt = get_system_prompt()
        self._tool = {
            "name": _TOOL_NAME,
            "description": "Output a complete NASM-informed training session plan.",
            "input_schema": TrainingSessionPlan.model_json_schema(),
        }

    def generate(self, inputs: dict) -> TrainingSessionPlan:
        user_prompt = build_user_prompt(inputs)

        @retry(
            stop=stop_after_attempt(config.MAX_RETRIES),
            wait=wait_fixed(config.RETRY_WAIT_SECONDS),
            retry=retry_if_not_exception_type(ValidationError),
            before_sleep=lambda rs: self._log.error(
                "API attempt %d failed: %s", rs.attempt_number, rs.outcome.exception()
            ),
        )
        def _attempt() -> TrainingSessionPlan:
            return self._call(user_prompt)

        return _attempt()

    def _call(self, user_prompt: str) -> TrainingSessionPlan:
        self._log.info("Calling API: model=%s", config.MODEL)

        response = self.client.messages.create(
            model=config.MODEL,
            max_tokens=config.MAX_TOKENS,
            temperature=config.TEMPERATURE_GENERATE,
            system=self._system_prompt,
            tools=[self._tool],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": user_prompt}],
        )

        self._log.info(
            "API response: input_tokens=%d, output_tokens=%d",
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

        tool_block = next(
            (b for b in response.content if b.type == "tool_use"),
            None,
        )
        if tool_block is None:
            raise ValueError("No tool_use block in API response")

        return TrainingSessionPlan.model_validate(tool_block.input)
