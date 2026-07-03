from __future__ import annotations

from abc import ABC, abstractmethod

from testagent.eval.models import EvalTask, GraderConfig, GraderResult, Transcript


class BaseGrader(ABC):
    """Abstract base class for all graders."""

    def __init__(self, config: GraderConfig) -> None:
        self.config = config

    @abstractmethod
    async def grade(
        self, transcript: Transcript, task: EvalTask, **kwargs
    ) -> GraderResult:
        """Grade a transcript against a task.

        Parameters
        ----------
        transcript:
            The execution transcript to grade.
        task:
            The evaluation task this transcript was generated for.
        **kwargs:
            Additional runtime context (e.g. an app session).

        Returns
        -------
        GraderResult
            The grading outcome.
        """
        ...
