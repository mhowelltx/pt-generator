import json
import logging

from anthropic import Anthropic
from pydantic import ValidationError
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_fixed

from app import config
from app.prompt_template import get_system_prompt, build_user_prompt
from app.schema import TrainingSessionPlan


def _strip_fences(text: str) -> str:
    """Remove markdown code fences that models sometimes wrap JSON in."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]  # drop the opening ```json line
        text = text.rsplit("```", 1)[0]  # drop the closing ```
    return text.strip()


class PlanGenerator:
    def __init__(self, client: Anthropic) -> None:
        self.client = client
        self._log = logging.getLogger(__name__)
        self._system_prompt = get_system_prompt()

    def generate(self, inputs: dict) -> TrainingSessionPlan:
        user_prompt = build_user_prompt(inputs)

        @retry(
            stop=stop_after_attempt(config.MAX_RETRIES),
            wait=wait_fixed(config.RETRY_WAIT_SECONDS),
            retry=retry_if_not_exception_type(ValidationError),
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
            messages=[{"role": "user", "content": user_prompt}],
        )

        if not response.content:
            raise ValueError("Empty response content from API")

        self._log.info(
            "API response: input_tokens=%d, output_tokens=%d",
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

        raw = _strip_fences(response.content[0].text)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._log.warning("Initial parse failed (%s). Attempting repair.", exc)
            self._log.debug("Failed raw text: %.500s", raw)
            data = self._repair(user_prompt)

        return TrainingSessionPlan.model_validate(data)

    def _repair(self, user_prompt: str) -> dict:
        """Fresh repair call — does not echo the bad output back to the model."""
        repair_prompt = user_prompt + "\n\nReturn ONLY valid JSON — no markdown, no commentary."

        self._log.info("Sending repair request: model=%s", config.MODEL)

        repair_response = self.client.messages.create(
            model=config.MODEL,
            max_tokens=config.MAX_TOKENS,
            temperature=config.TEMPERATURE_REPAIR,
            system=self._system_prompt,
            messages=[{"role": "user", "content": repair_prompt}],
        )

        if not repair_response.content:
            raise ValueError("Empty repair response content from API")

        self._log.info(
            "Repair response: input_tokens=%d, output_tokens=%d",
            repair_response.usage.input_tokens,
            repair_response.usage.output_tokens,
        )

        raw2 = _strip_fences(repair_response.content[0].text)

        try:
            return json.loads(raw2)
        except json.JSONDecodeError as exc:
            self._log.error("Repair parse also failed (%s). Raw: %.500s", exc, raw2)
            raise
