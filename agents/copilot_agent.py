from __future__ import annotations

from typing import Any

from pydantic_ai import Agent

from agents.llm_common import compact_json, resolve_model_name


class CopilotAgent:
    """Operator copilot for explaining workflow outcomes and next steps."""

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = resolve_model_name(model_name)
        self.agent = Agent(
            self.model_name,
            instructions=(
                "You are the AI copilot for a multi-agent equity trading platform. "
                "Help operators understand workflow results, risks, execution outcomes, and sensible next actions. "
                "Be concise, practical, and honest about uncertainty. "
                "Do not claim that trades were executed unless the workflow state says execution_status is SUBMITTED. "
                "If no workflow state is available, explain that the user should run a ticker first."
            ),
        )

    async def answer(self, question: str, workflow_state: dict[str, Any] | None) -> str:
        state_block = (
            compact_json(workflow_state) if workflow_state else "No workflow state is available yet for this session."
        )
        prompt = (
            "Answer the operator's question using the latest platform state.\n\n"
            f"Operator question:\n{question.strip()}\n\n"
            f"Latest workflow state:\n{state_block}\n\n"
            "Response guidance:\n"
            "- Keep the answer directly tied to the current platform state.\n"
            "- Mention concrete fields like signal, confidence, risk, and execution when relevant.\n"
            "- Suggest a next step when useful.\n"
            "- If the state is missing, say so plainly."
        )
        result = await self.agent.run(prompt)
        return str(result.output).strip()
