from __future__ import annotations

import json
import logging
import os
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Literal

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
DEFAULT_EXECUTION_PROVIDER = "alpaca_rest"
DEFAULT_MCP_COMMAND = "npx"
DEFAULT_MCP_ARGS = ["-y", "@alpaca/mcp-server"]
FALLBACK_MCP_COMMAND = "uvx"
FALLBACK_MCP_ARGS = ["alpaca-mcp-server"]
DEFAULT_UV_CACHE_DIR = Path(__file__).resolve().parents[1] / ".uv-cache"


class ExecutionError(RuntimeError):
    """Raised when order placement fails."""


@dataclass(slots=True)
class ExecutionResult:
    status: str
    detail: str
    order_response: Dict[str, Any] | None = None
    tool_name_used: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ExecutionAgent:
    """Executes approved trades via Alpaca REST by default, with optional MCP fallback."""

    def __init__(
        self,
        execution_provider: Literal["alpaca_rest", "mcp"] | None = None,
        mcp_command: str = DEFAULT_MCP_COMMAND,
        mcp_args: list[str] | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.execution_provider = (execution_provider or os.getenv("EXECUTION_PROVIDER", DEFAULT_EXECUTION_PROVIDER)).strip()
        self.mcp_command = os.getenv("ALPACA_MCP_COMMAND", mcp_command)
        self.mcp_args = self._resolve_args(mcp_args)
        self.timeout_seconds = timeout_seconds

    async def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        ticker = state.get("ticker", "UNKNOWN")
        risk_approved = bool(state.get("risk_approved", False))
        share_count = int(state.get("share_count", 0) or 0)
        signal = state.get("signal", "HOLD")
        broker_connection = state.get("broker_connection")

        if not risk_approved or signal != "BUY" or share_count < 1:
            detail = (
                f"Execution skipped for {ticker}: risk_approved={risk_approved}, signal={signal}, share_count={share_count}."
            )
            logger.info("[ExecutionAgent] %s", detail)
            return self._result_to_state(ExecutionResult(status="SKIPPED", detail=detail))

        if not broker_connection:
            detail = (
                f"Execution skipped for {ticker}: no connected broker account is available. "
                "Sign in and connect an Alpaca paper account to place trades."
            )
            logger.info("[ExecutionAgent] %s", detail)
            return self._result_to_state(
                ExecutionResult(status="SKIPPED", detail=detail, tool_name_used=self.execution_provider)
            )

        logger.info(
            "[ExecutionAgent] Preparing execution for %s qty=%s provider=%s",
            ticker,
            share_count,
            self.execution_provider,
        )

        try:
            if self.execution_provider == "mcp":
                order_response, tool_name = await self._place_order_via_mcp(
                    ticker=ticker,
                    qty=share_count,
                    broker_connection=broker_connection,
                )
            else:
                order_response, tool_name = await self._place_order_via_rest(
                    ticker=ticker,
                    qty=share_count,
                    broker_connection=broker_connection,
                )

            detail = f"Order submitted for {ticker}. qty={share_count} tool={tool_name}."
            logger.info("[ExecutionAgent] %s", detail)
            return self._result_to_state(
                ExecutionResult(
                    status="SUBMITTED",
                    detail=detail,
                    order_response=order_response,
                    tool_name_used=tool_name,
                )
            )
        except Exception as exc:
            logger.exception("[ExecutionAgent] Order submission failed for %s", ticker)
            return self._result_to_state(
                ExecutionResult(
                    status="FAILED",
                    detail=f"Order submission failed for {ticker}: {exc}",
                    order_response=None,
                    tool_name_used=self.execution_provider,
                )
            )

    async def _place_order_via_rest(
        self,
        ticker: str,
        qty: int,
        broker_connection: Dict[str, Any],
    ) -> tuple[Dict[str, Any], str]:
        url = f"{ALPACA_PAPER_BASE_URL}/v2/orders"
        headers = {
            "APCA-API-KEY-ID": broker_connection["api_key"],
            "APCA-API-SECRET-KEY": broker_connection["secret_key"],
        }
        payload = {
            "symbol": ticker,
            "qty": qty,
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)

        if response.status_code not in (200, 201):
            raise ExecutionError(
                f"Alpaca REST order failed with status={response.status_code} body={response.text[:300]}"
            )

        parsed = response.json()
        return parsed, "alpaca_rest"

    async def _place_order_via_mcp(
        self,
        ticker: str,
        qty: int,
        broker_connection: Dict[str, Any],
    ) -> tuple[Dict[str, Any], str]:
        first_attempt = (self.mcp_command, self.mcp_args)
        fallback_attempt = (FALLBACK_MCP_COMMAND, FALLBACK_MCP_ARGS)
        attempts = [first_attempt]
        if first_attempt != fallback_attempt:
            attempts.append(fallback_attempt)

        last_error: Exception | None = None
        for command, args in attempts:
            try:
                result = await self._place_order_with_server(
                    command=command,
                    args=args,
                    ticker=ticker,
                    qty=qty,
                    broker_connection=broker_connection,
                )
                return result
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "[ExecutionAgent] MCP connection/order attempt failed via '%s %s': %s",
                    command,
                    args,
                    exc,
                )

        raise ExecutionError(f"All MCP execution attempts failed: {last_error}")

    async def _place_order_with_server(
        self,
        command: str,
        args: list[str],
        ticker: str,
        qty: int,
        broker_connection: Dict[str, Any],
    ) -> tuple[Dict[str, Any], str]:
        env = {
            "ALPACA_API_KEY": broker_connection["api_key"],
            "ALPACA_SECRET_KEY": broker_connection["secret_key"],
            "ALPACA_PAPER_TRADE": "true",
            "ALPACA_BASE_URL": ALPACA_PAPER_BASE_URL,
        }
        if command == "uvx":
            uv_cache_dir = Path(os.getenv("UV_CACHE_DIR", str(DEFAULT_UV_CACHE_DIR)))
            uv_cache_dir.mkdir(parents=True, exist_ok=True)
            env["UV_CACHE_DIR"] = str(uv_cache_dir)

        server_params = StdioServerParameters(command=command, args=args, env=env)

        async with AsyncExitStack() as stack:
            read_stream, write_stream = await stack.enter_async_context(stdio_client(server_params))
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()

            tools_result = await session.list_tools()
            available_tools = {tool.name for tool in tools_result.tools}
            logger.info("[ExecutionAgent] Available MCP tools: %s", sorted(available_tools))

            tool_name = self._pick_tool_name(available_tools)
            arguments = self._build_order_args(tool_name=tool_name, ticker=ticker, qty=qty)

            logger.info("[ExecutionAgent] Calling MCP tool=%s arguments=%s", tool_name, arguments)
            tool_result = await session.call_tool(tool_name, arguments=arguments)
            parsed = self._parse_tool_result(tool_result)
            return parsed, tool_name

    @staticmethod
    def _pick_tool_name(available_tools: Iterable[str]) -> str:
        preferred = ["place_stock_order", "create_order", "submit_order", "place_order"]
        available_tools = set(available_tools)
        for name in preferred:
            if name in available_tools:
                return name
        raise ExecutionError(
            f"Could not find a supported stock order tool. Available tools: {sorted(available_tools)}"
        )

    @staticmethod
    def _build_order_args(tool_name: str, ticker: str, qty: int) -> Dict[str, Any]:
        return {
            "symbol": ticker,
            "qty": qty,
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
        }

    @staticmethod
    def _parse_tool_result(tool_result: Any) -> Dict[str, Any]:
        if hasattr(tool_result, "structuredContent") and tool_result.structuredContent:
            structured = tool_result.structuredContent
            if isinstance(structured, dict):
                return structured

        if hasattr(tool_result, "content"):
            for item in tool_result.content:
                text = getattr(item, "text", None)
                if not text:
                    continue
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"raw_text": text}

        if isinstance(tool_result, dict):
            return tool_result

        return {"raw_result": repr(tool_result)}

    def _resolve_args(self, mcp_args: list[str] | None) -> list[str]:
        if mcp_args is not None:
            return mcp_args
        env_args = os.getenv("ALPACA_MCP_ARGS")
        if env_args:
            return [arg for arg in env_args.split(" ") if arg]
        return list(DEFAULT_MCP_ARGS)

    @staticmethod
    def _result_to_state(result: ExecutionResult) -> Dict[str, Any]:
        data = result.to_dict()
        return {
            "execution_status": data["status"],
            "execution_detail": data["detail"],
            "order_response": data["order_response"],
            "execution_tool": data["tool_name_used"],
        }
