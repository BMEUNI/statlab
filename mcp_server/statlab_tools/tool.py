"""The `@tool` decorator: registration, envelope, exception classification, timing, and the
D17 fit semaphore. Every later tool (~85 of them) is a function wearing this decorator; a wrong
shape here is eighty call sites to fix later, so the decorator owns everything a tool body must
not repeat.

PLAN.md §6.1, §6.1.1, §6.2, D17, D22, D23.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Callable, Sequence
from typing import Annotated, Final, get_args, get_origin, get_type_hints

import anyio
from mcp.server.mcpserver import MCPServer
from pydantic import Field
from pydantic.fields import FieldInfo
from statlab_contracts.envelope import ErrorClass, ToolFailure, ToolResult, ToolSuccess
from statlab_contracts.layout import RunPaths

from statlab_tools.artifacts import ToolArtifactError
from statlab_tools.settings import get_tool_settings

logger = logging.getLogger("statlab_tools")

server: Final = MCPServer(name="statlab-tools", version="0.1.0")
"""The one MCP server instance. `tools/__init__.py` imports every tool module, which
decorates functions onto this server; `server.py` imports this module (transitively, via
`tools`) so the app it builds serves the same registry."""

_FIT_SEMAPHORE_CACHE: dict[int, anyio.Semaphore] = {}
"""One semaphore per permit count. In practice there is exactly one entry -- `get_tool_settings`
is `lru_cache`d -- but keying on the count rather than hardcoding a module-level singleton
keeps tests free to construct settings with a different `fit_permits` without import-order
tricks."""


def _fit_semaphore() -> anyio.Semaphore:
    permits = get_tool_settings().effective_fit_permits
    semaphore = _FIT_SEMAPHORE_CACHE.get(permits)
    if semaphore is None:
        semaphore = anyio.Semaphore(permits)
        _FIT_SEMAPHORE_CACHE[permits] = semaphore
    return semaphore


class ToolParamError(Exception):
    """Schema-valid but semantically wrong arguments (§6.1.1). Classified as `tool_param`.

    `field_errors` is mandatory -- `Failure`'s own validator already enforces this on the
    envelope, but requiring it here too means a tool body cannot construct a
    `ToolParamError` that would fail to become a valid `ToolFailure` later.
    """

    def __init__(self, message: str, *, field_errors: dict[str, str]) -> None:
        if not field_errors:
            raise ValueError("field_errors must be non-empty for ToolParamError")
        super().__init__(message)
        self.message = message
        self.field_errors = field_errors


class ToolDataError(Exception):
    """The data cannot support the request -- not repairable by retrying (§6.1.1).
    Classified as `data`. An argument-shape problem with no possible fix through different
    arguments (e.g. "no numeric column exists") is this, never `ToolParamError`."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


_TRANSIENT_EXCEPTIONS: Final = (ToolArtifactError, OSError, TimeoutError)
"""§6.1.1's `transient` row: IO and timeouts. `ToolArtifactError` covers content-IO failures
from `statlab_tools.artifacts`; `OSError`/`TimeoutError` cover everything else at that shape."""

ORCHESTRATOR_SUPPLIED: Final = "Supplied by the orchestrator; do not choose a value."
"""Description for values supplied by the orchestrator rather than selected by the model."""

ToolFn = Callable[..., ToolSuccess]


def _classify(exc: Exception) -> ErrorClass:
    """§6.1.1's table, applied to a caught exception. `bug` is the default, not a fallback of
    convenience -- an exception this decorator does not recognise really is unexpected."""
    if isinstance(exc, ToolParamError):
        return "tool_param"
    if isinstance(exc, ToolDataError):
        return "data"
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):
        return "transient"
    return "bug"


def _failure_from_exception(exc: Exception) -> ToolFailure:
    """Builds the envelope's failure half. Never includes a traceback -- §6.1.1: a traceback
    goes to the server log only. The caller (the wrapper) is responsible for logging it;
    this function only ever sees the message, never `sys.exc_info()`.

    Every branch but `ToolParamError` uses `str(exc)`, not a `.message` attribute -- for
    `ToolArtifactError` specifically, `__str__` prefixes the path (`f"{self.path}: {message}"`),
    and a transient-IO failure without the path it happened on is a message the agent cannot
    act on. `ToolDataError` and the fallback both already reduce to plain `str(exc)`; writing
    them as one branch avoids maintaining three lines that would silently diverge later."""
    error_class = _classify(exc)
    if isinstance(exc, ToolParamError):
        return ToolFailure(
            error_class=error_class, message=exc.message, field_errors=exc.field_errors
        )
    return ToolFailure(error_class=error_class, message=str(exc))


def _run_paths_param_names(hints: dict[str, object]) -> list[str]:
    """Parameters whose *resolved* annotation is exactly `RunPaths`, found by type rather than
    by the name `paths` (§8.2 of the addendum). Name matching gets three real shapes wrong: a
    `RunPaths` parameter named anything else is missed; a `str` parameter that happens to be
    named `paths` has a real wire parameter silently stripped; and two `RunPaths` parameters
    are undetectable as a conflict at all. Type matching, using the same
    `get_type_hints(..., include_extras=True)` resolution already required for postponed
    annotations, gets all three right."""
    return [name for name, hint in hints.items() if hint is RunPaths]


def _check_run_paths_param_count(
    func: ToolFn, hints: dict[str, object], *, needs_run: bool
) -> str | None:
    """Raises at decoration time if the number of `RunPaths`-typed parameters does not match
    `needs_run` -- exactly one when `needs_run=True`, exactly zero when `needs_run=False`.
    Converts what used to be a naming convention into a checked invariant, the same posture as
    T3/T4 failing the build for a missing description. Returns the single parameter's name
    when `needs_run=True`, else `None`."""
    names = _run_paths_param_names(hints)
    if needs_run:
        if len(names) != 1:
            raise ValueError(
                f"tool {func.__name__!r} has {len(names)} RunPaths-typed parameters, "
                f"expected exactly 1 for needs_run=True"
            )
        return names[0]
    if names:
        raise ValueError(
            f"tool {func.__name__!r} has {len(names)} RunPaths-typed parameter(s), "
            f"expected 0 for needs_run=False"
        )
    return None


def _field_info(annotation: object) -> FieldInfo | None:
    """Extracts the `FieldInfo` from `Annotated[T, Field(...)]`, or `None` if the parameter is
    not annotated that way at all (a plain `int`, say)."""
    if get_origin(annotation) is not Annotated:
        return None
    for arg in get_args(annotation)[1:]:
        if isinstance(arg, FieldInfo):
            return arg
    return None


def _check_docstring(func: ToolFn) -> str:
    """D23: the schema is the agent's only documentation. A tool with no docstring is a defect
    that must fail the build, not ship and be discovered by a confused agent at runtime."""
    doc = inspect.getdoc(func)
    if not doc:
        raise ValueError(f"tool {func.__name__!r} has no docstring; D23 requires one")
    return doc


def _check_parameter_descriptions(
    func: ToolFn, hints: dict[str, object], *, run_paths_param: str | None
) -> None:
    """D23, same reasoning: every parameter must carry `Field(description=...)`. Enforced once
    here, at decoration time, so all ~85 later tools get it right without each remembering to
    check.

    Uses `get_type_hints(..., include_extras=True)`, not raw `parameter.annotation` --  every
    tool module has `from __future__ import annotations` (project convention), so annotations
    are plain strings at runtime and `get_origin`/`get_args` would never see `Annotated` without
    this resolution step.

    Skips `run_paths_param` -- the one parameter (if any) identified as `RunPaths` by type via
    `_check_run_paths_param_count`, not by checking whether its name happens to be `"paths"`
    (§8.2 of the addendum: a `RunPaths` parameter may be named anything, and a parameter merely
    *named* `paths` that is not a `RunPaths` must still be checked like any other).
    """
    signature = inspect.signature(func)
    for name in signature.parameters:
        if name == run_paths_param:
            continue
        field = _field_info(hints.get(name))
        if field is None or not field.description:
            raise ValueError(
                f"tool {func.__name__!r} parameter {name!r} has no Field(description=...)"
            )


def _inject_run_params(func: ToolFn, *, run_paths_param: str | None) -> Callable[..., ToolSuccess]:
    """Wraps `func` so it is called with `project`/`run` stripped and a resolved `RunPaths`
    passed as `run_paths_param` instead. The schema the MCP server sees is built separately, by
    `_build_schema_fn`; this function only changes what is called at request time.

    `run_paths_param` is the parameter identified as `RunPaths`-typed by
    `_check_run_paths_param_count`, not a hardcoded `"paths"` -- the body's `RunPaths`
    parameter may be named anything (§8.2 of the addendum). Only ever called with
    `needs_run=True`, where `_check_run_paths_param_count` already guarantees a real name; the
    `None` case below is a genuine defensive check on that guarantee, not dead code -- it is
    exercised directly in tests rather than only relied upon."""
    if run_paths_param is None:
        raise ValueError("_inject_run_params requires a resolved RunPaths parameter name")

    def resolve_and_call(*, project: str, run: str, **kwargs: object) -> ToolSuccess:
        settings = get_tool_settings()
        paths = RunPaths(data_dir=settings.data_dir, slug=project, run_id=run)
        if not paths.root.is_dir():
            raise ToolArtifactError(str(paths.root), "run directory does not exist")
        return func(**{run_paths_param: paths}, **kwargs)

    return resolve_and_call


def _build_schema_fn(
    func: ToolFn, *, needs_run: bool, run_paths_param: str | None
) -> Callable[..., ToolSuccess]:
    """Builds the function the MCP server actually introspects for its schema: `func`'s own
    parameters, minus the `RunPaths` one (an implementation detail, never on the wire), plus
    `project` and `run` when `needs_run`. Replacing a thin passthrough's signature is what makes
    the *served* signature differ from the *body's* signature without hand-writing a schema
    (D23's own rule: schemas are generated, never hand-written).

    Parameter annotations are resolved via `get_type_hints(..., include_extras=True)`, not
    copied from `inspect.signature` directly -- every tool module has
    `from __future__ import annotations`, so the raw annotations are plain strings and the MCP
    SDK's own schema generator needs real `Annotated[...]` objects to read `Field` metadata
    from.

    `run_paths_param` is the name to strip, found by type via `_check_run_paths_param_count` --
    not hardcoded to `"paths"` (§8.2 of the addendum).
    """
    original_signature = inspect.signature(func)
    hints = get_type_hints(func, include_extras=True)
    kept_params = [
        p.replace(annotation=hints[name], kind=inspect.Parameter.KEYWORD_ONLY)
        for name, p in original_signature.parameters.items()
        if name != run_paths_param
    ]

    if needs_run:
        project_param = inspect.Parameter(
            "project",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[str, Field(description=ORCHESTRATOR_SUPPLIED)],
        )
        run_param = inspect.Parameter(
            "run",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[str, Field(description=ORCHESTRATOR_SUPPLIED)],
        )
        kept_params = [project_param, run_param, *kept_params]

    def schema_fn(**kwargs: object) -> ToolSuccess:
        raise NotImplementedError("schema_fn exists only for signature introspection")

    schema_fn.__signature__ = original_signature.replace(  # type: ignore[attr-defined]  # stub gap: assigning __signature__ is how inspect.signature is overridden and the runtime honours it, but mypy's function type does not declare the attribute writable
        parameters=kept_params, return_annotation=ToolSuccess | ToolFailure
    )
    return schema_fn


def _call_as_object(called: Callable[..., ToolSuccess], kwargs: dict[str, object]) -> object:
    """Calls `called` and returns its result typed as `object`, not `ToolSuccess`.

    `called`'s static return type is `ToolSuccess`, but the very reason the wrapper checks
    `isinstance(result, ToolFailure)` is that a misbehaving body's *actual* return need not
    match its annotation (T14 tests exactly this). Trusting the static type at the call site
    would make mypy treat that check as unreachable -- correct under the type system, wrong
    about what can happen at runtime when a body violates its own contract.
    """
    return called(**kwargs)


def tool(
    *,
    phase: str,
    allow_in: Sequence[str],
    heavy: bool = False,
    needs_run: bool = True,
) -> Callable[[ToolFn], ToolFn]:
    """Register a tool with the MCP server, wrapping it in the uniform envelope (§6.1.1).

    The decorated function contains only its own logic. This wrapper owns: registration,
    `phase`/`allow_in`/`heavy` metadata, run-path resolution, the result envelope, exception
    classification, timing, and the D17 semaphore for `heavy=True`.

    A tool body takes `paths: RunPaths` (when `needs_run=True`) plus its own declared
    parameters, and returns `ToolSuccess`. It never returns `ToolFailure` -- raising is the
    contract; `ToolParamError`/`ToolDataError` for the two classifiable cases, anything else
    for `transient`/`bug`.

    `needs_run=False` is for tools with no artifact input -- `health`-adjacent utilities. It
    suppresses the injected `project`/`run` parameters and calls the body directly with no
    `paths` argument.
    """

    def decorator(func: ToolFn) -> ToolFn:
        description = _check_docstring(func)
        hints = get_type_hints(func, include_extras=True)
        run_paths_param = _check_run_paths_param_count(func, hints, needs_run=needs_run)
        _check_parameter_descriptions(func, hints, run_paths_param=run_paths_param)

        if server._tool_manager.get_tool(func.__name__) is not None:
            raise ValueError(f"a tool named {func.__name__!r} is already registered")

        called = _inject_run_params(func, run_paths_param=run_paths_param) if needs_run else func
        schema_source = _build_schema_fn(func, needs_run=needs_run, run_paths_param=run_paths_param)

        async def wrapper(**kwargs: object) -> ToolResult:
            started = time.monotonic()

            def call_body() -> object:
                return _call_as_object(called, kwargs)

            try:
                # Bodies are synchronous and CPU- or IO-bound. Running them inline would block
                # the event loop and serialise every tool call across every run, defeating
                # D17 (runs proceed in parallel and serialise only at heavy fits). The
                # semaphore is acquired on the loop, so a queued heavy call holds no thread.
                if heavy:
                    async with _fit_semaphore():
                        result = await anyio.to_thread.run_sync(call_body)
                else:
                    result = await anyio.to_thread.run_sync(call_body)
                if isinstance(result, ToolFailure):
                    # §6.1.1: tool bodies raise, they never return ToolFailure. One conversion
                    # point (this except block, below) beats eighty; a body returning a
                    # ToolFailure directly bypassed it, so treat that as the bug it is.
                    raise TypeError(
                        f"tool {func.__name__!r} returned ToolFailure directly; raise instead"
                    )
            except Exception as exc:
                duration_ms = (time.monotonic() - started) * 1000
                failure = _failure_from_exception(exc)
                logger.warning(
                    "tool %s failed after %.1fms: %s",
                    func.__name__,
                    duration_ms,
                    failure.message,
                    exc_info=(failure.error_class == "bug"),
                )
                return failure
            if not isinstance(result, ToolSuccess):
                # A body returned neither ToolSuccess nor ToolFailure -- the T14 check above
                # already ruled out the latter, so this is some other contract violation (e.g.
                # a bare dict). Treat it the same way: a bug, not a silent pass-through.
                duration_ms = (time.monotonic() - started) * 1000
                failure = ToolFailure(
                    error_class="bug",
                    message=(
                        f"tool {func.__name__!r} returned {type(result).__name__}, not ToolSuccess"
                    ),
                )
                logger.warning(
                    "tool %s failed after %.1fms: %s", func.__name__, duration_ms, failure.message
                )
                return failure
            duration_ms = (time.monotonic() - started) * 1000
            logger.info("tool %s succeeded in %.1fms", func.__name__, duration_ms)
            return result

        wrapper.__signature__ = inspect.signature(schema_source)  # type: ignore[attr-defined]  # same stub gap as schema_fn above
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = description

        server.tool(
            description=description,
            meta={"phase": phase, "allow_in": list(allow_in), "heavy": heavy},
        )(wrapper)

        return func

    return decorator
