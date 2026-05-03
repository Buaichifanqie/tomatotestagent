from testagent.harness.runners.base import BaseRunner, IRunner, RunnerError, RunnerFactory, UnknownTaskTypeError
from testagent.harness.runners.http_runner import HTTPRunner
from testagent.harness.runners.playwright_runner import PlaywrightRunner

RunnerFactory.register("api_test", HTTPRunner)
RunnerFactory.register("web_test", PlaywrightRunner)

__all__ = [
    "BaseRunner",
    "HTTPRunner",
    "IRunner",
    "PlaywrightRunner",
    "RunnerError",
    "RunnerFactory",
    "UnknownTaskTypeError",
]
