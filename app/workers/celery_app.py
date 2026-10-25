"""Celery application configuration and portable task wrapper for ReconcileX.

In production deployments with `celery` and `redis` installed, initializes a full
Celery application configured with JSON serialization, `task_acks_late=True`,
`worker_prefetch_multiplier=1`, and bounded retry policies.

In lightweight local or CI environments where `celery` is not installed, provides
a compatible `LocalCeleryApp` fallback supporting `.task()`, `.delay()`, and
`.apply_async()` so all background tasks remain testable without a live Redis broker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from app.core.config import get_settings

settings = get_settings()

try:
    from celery import Celery  # type: ignore

    HAS_CELERY = True
except ImportError:
    HAS_CELERY = False


@dataclass
class EagerAsyncResult:
    """Result object returned when executing tasks in eager/local mode."""

    id: str
    status: str
    result: Any = None


class LocalTaskWrapper:
    """Task wrapper providing Celery-compatible `.delay()` and `.apply_async()` methods."""

    def __init__(self, func: Callable[..., Any], name: str, max_retries: int = 3) -> None:
        self._func = func
        self.name = name
        self.max_retries = max_retries
        self.__name__ = func.__name__
        self.__doc__ = func.__doc__

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._func(*args, **kwargs)

    def delay(self, *args: Any, **kwargs: Any) -> EagerAsyncResult:
        return self.apply_async(args=args, kwargs=kwargs)

    def apply_async(
        self,
        args: tuple[Any, ...] = (),
        kwargs: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
        **_options: Any,
    ) -> EagerAsyncResult:
        kw = kwargs or {}
        res = self._func(*args, **kw)
        tid = task_id or (str(args[0]) if args else "eager-task")
        return EagerAsyncResult(id=tid, status="SUCCESS", result=res)


class LocalCeleryApp:
    """Fallback Celery-compatible application for environments without `celery` installed."""

    def __init__(self, main: str) -> None:
        self.main = main
        self.conf: Dict[str, Any] = {
            "task_serializer": "json",
            "accept_content": ["json"],
            "result_serializer": "json",
            "timezone": "UTC",
            "enable_utc": True,
            "task_acks_late": True,
            "worker_prefetch_multiplier": 1,
            "task_always_eager": True,
        }
        self.tasks: Dict[str, LocalTaskWrapper] = {}

    def task(
        self,
        name: Optional[str] = None,
        max_retries: int = 3,
        **_kwargs: Any,
    ) -> Callable[[Callable[..., Any]], LocalTaskWrapper]:
        def _decorator(func: Callable[..., Any]) -> LocalTaskWrapper:
            task_name = name or f"{self.main}.{func.__name__}"
            wrapped = LocalTaskWrapper(func=func, name=task_name, max_retries=max_retries)
            self.tasks[task_name] = wrapped
            return wrapped

        return _decorator


def create_celery_app() -> Any:
    """Create and configure the Celery application instance."""
    cfg = get_settings()
    if HAS_CELERY:
        app = Celery(
            "reconcilex",
            broker=cfg.CELERY_BROKER_URL,
            backend=cfg.CELERY_RESULT_BACKEND,
            include=["app.workers.tasks"],
        )
        app.conf.update(
            task_serializer="json",
            accept_content=["json"],
            result_serializer="json",
            timezone="UTC",
            enable_utc=True,
            task_acks_late=True,
            worker_prefetch_multiplier=1,
            task_time_limit=300,
            task_soft_time_limit=240,
            task_always_eager=cfg.CELERY_TASK_ALWAYS_EAGER,
        )
        return app
    return LocalCeleryApp("reconcilex")


celery_app = create_celery_app()
