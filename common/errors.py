from __future__ import annotations


class TestAgentError(Exception):
    __test__ = False

    def __init__(
        self,
        message: str,
        code: str = "UNKNOWN",
        details: dict[str, object] | None = None,
    ) -> None:
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(message)

    def __str__(self) -> str:
        parts: list[str] = [f"[{self.code}] {self.message}"]
        if self.details:
            parts.append(f" details={self.details}")
        return "".join(parts)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(message={self.message!r}, code={self.code!r}, details={self.details!r})"


class ConfigError(TestAgentError):
    pass


class DatabaseError(TestAgentError):
    pass


class AgentError(TestAgentError):
    pass


class AgentTimeoutError(AgentError):
    pass


class AgentContextOverflowError(AgentError):
    pass


class MCPError(TestAgentError):
    pass


class MCPConnectionError(MCPError):
    pass


class MCPToolError(MCPError):
    pass


class MCPServerUnavailableError(MCPError):
    pass


class RAGError(TestAgentError):
    pass


class RAGIngestionError(RAGError):
    pass


class RAGSearchError(RAGError):
    pass


class RAGDegradedError(RAGError):
    pass


class HarnessError(TestAgentError):
    pass


class SandboxError(HarnessError):
    pass


class SandboxTimeoutError(SandboxError):
    pass


class SandboxResourceError(SandboxError):
    pass


class SkillError(TestAgentError):
    pass


class SkillParseError(SkillError):
    pass


class SkillValidationError(SkillError):
    pass


class SkillDegradedError(SkillError):
    pass


class LLMError(TestAgentError):
    pass


class LLMRateLimitError(LLMError):
    pass


class LLMTokenLimitError(LLMError):
    pass
