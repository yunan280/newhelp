"""Request-local roots; telemetry failures never change a business result."""
import asyncio
import logging
from contextlib import ExitStack, aclosing, contextmanager
from contextvars import ContextVar
from functools import wraps
from uuid import uuid4

from .contracts import RequestTraceContext

logger = logging.getLogger(__name__)
_current_request = ContextVar('ch09_request', default=None)
_active_runtime = ContextVar('ch09_observation_runtime', default=None)
_default_runtime = None


class RequestObservation:
    def __init__(self, span=None, *, metadata=None):
        self.span = span
        self.trace_id = span.trace_id if span is not None else None
        self.metadata = {**(metadata or {}), 'intent': '未分类', 'status': 'running'}
        self.finished = False

    def update(self, **kwargs):
        if self.span is not None:
            try:
                self.span.update(**kwargs)
            except Exception:
                logger.warning('Langfuse observation update failed', exc_info=True)

    def bind(self, **metadata):
        self.metadata.update({k: v for k, v in metadata.items() if v is not None})
        self.update(metadata=self.metadata)

    def set_intent(self, intent: str):
        self.bind(intent=intent or '其他')

    def finish(self, *, status: str, output: dict):
        self.finished = True
        self.bind(status=status)
        self.update(output=output, level='ERROR' if status == 'error' else 'DEFAULT')
        if self.span is not None:
            try:
                from langfuse import propagate_attributes
                with propagate_attributes(metadata=self.metadata,
                                          session_id=self.metadata.get('session_id'),
                                          user_id=self.metadata.get('user_id')):
                    pass
            except Exception:
                logger.warning('Langfuse final trace metadata failed', exc_info=True)


class ObservationRuntime:
    def __init__(self, *, settings=None, client=None, public_key=None, callback_factory=None):
        self.client = client
        self.callbacks = ()
        if client is None and settings is not None and settings.enabled:
            from langfuse import Langfuse
            self.client = Langfuse(public_key=settings.public_key, secret_key=settings.secret_key,
                                   base_url=settings.base_url, environment='ch09-local', timeout=5)
        if self.client is not None:
            from .costs import ProviderUsageCallback
            factory = callback_factory or ProviderUsageCallback
            self.callbacks = (factory(public_key=public_key or
                                       (settings.public_key if settings else None)),)

    @contextmanager
    def observe(self, name: str, *, input: dict, kind: str = 'span'):
        stack = ExitStack()
        span = None
        if self.client is not None:
            try:
                span = stack.enter_context(self.client.start_as_current_observation(
                    name=name, as_type=kind, input=input))
            except Exception:
                stack.close()
                logger.warning('Langfuse observation start failed', exc_info=True)
        observation = RequestObservation(span)
        try:
            yield observation
        except BaseException as exc:
            observation.update(level='ERROR', status_message=type(exc).__name__)
            raise
        finally:
            try:
                stack.close()
            except Exception:
                logger.warning('Langfuse observation close failed', exc_info=True)

    @contextmanager
    def request(self, ctx: RequestTraceContext, *, input: dict):
        stack = ExitStack()
        root, context_token = None, None
        metadata = {**ctx.metadata(), 'intent': '未分类', 'status': 'running'}
        if self.client is not None:
            try:
                from langfuse import propagate_attributes
                from opentelemetry import context
                # asyncio tasks inherit context. Every HTTP/background request owns a root.
                context_token = context.attach(context.Context())
                stack.enter_context(propagate_attributes(
                    session_id=ctx.session_id, user_id=ctx.user_id,
                    metadata=metadata, trace_name='mewhelp.' + ctx.entry_point))
                root = stack.enter_context(self.client.start_as_current_observation(
                    name='mewhelp.' + ctx.entry_point, input=input, metadata=metadata))
            except Exception:
                stack.close()
                root = None
                logger.warning('Langfuse request start failed', exc_info=True)
        observation = RequestObservation(root, metadata=metadata)
        request_token = _current_request.set(observation)
        runtime_token = _active_runtime.set(self)
        try:
            yield observation
        except (GeneratorExit, asyncio.CancelledError):
            observation.finish(status='disconnected', output={})
            raise
        except BaseException as exc:
            observation.finish(status='error', output={'error_type': type(exc).__name__})
            raise
        finally:
            if not observation.finished:
                observation.finish(status='completed', output={})
            _current_request.reset(request_token)
            _active_runtime.reset(runtime_token)
            try:
                stack.close()
            except Exception:
                logger.warning('Langfuse request close failed', exc_info=True)
            finally:
                if context_token is not None:
                    context.detach(context_token)

    def flush(self):
        self._export('flush')

    def shutdown(self):
        self._export('shutdown')

    def _export(self, method):
        if self.client is not None:
            try:
                getattr(self.client, method)()
            except Exception:
                logger.warning('Langfuse %s failed', method, exc_info=True)


_disabled = ObservationRuntime()


def current_request():
    return _current_request.get()


def get_observation_runtime():
    return _active_runtime.get() or _default_runtime or _disabled


def set_default_runtime(runtime):
    global _default_runtime
    previous = _default_runtime
    _default_runtime = runtime
    return previous


def model_config():
    """Graph-internal calls inherit callbacks; only legacy outside calls need this."""
    return {'callbacks': list(get_observation_runtime().callbacks)}


def model_kwargs():
    callbacks = get_observation_runtime().callbacks
    return {'config': {'callbacks': list(callbacks)}} if callbacks else {}


def with_callbacks(model):
    return model.with_config(model_config()) if get_observation_runtime().callbacks else model


def observed_sync_call(name, input, function, *args, **kwargs):
    from dataclasses import asdict, is_dataclass
    with get_observation_runtime().observe(name, input=input, kind='retriever') as span:
        result = function(*args, **kwargs)
        span.update(output=asdict(result) if is_dataclass(result) else result)
        return result


def trace_graph_stream(entry_point):
    """Keep context alive until an event generator is exhausted or explicitly closed."""
    def decorate(function):
        @wraps(function)
        async def wrapped(runtime, request, *args, **kwargs):
            observations = getattr(runtime.context, 'observation_runtime', None)
            observations = observations or get_observation_runtime()
            ctx = RequestTraceContext(session_id=request.session_id,
                user_id=request.resolved_user_id, entry_point=entry_point,
                turn_id=uuid4().hex if entry_point in {'agent', 'chat_stream'} else None)
            data = request.model_dump(mode='json')
            with observations.request(ctx, input=data) as root:
                root.bind(graph_bound=True)
                async with aclosing(function(runtime, request, *args, **kwargs)) as stream:
                    async for item in stream:
                        event_data = item.get('data', {})
                        if item.get('event') == 'session':
                            root.bind(session_id=event_data.get('session_id'),
                                      conversation_id=event_data.get('conversation_id'))
                        elif item.get('event') in {'done', 'waiting_for_order', 'waiting_for_ticket'}:
                            if event_data.get('intent'):
                                root.set_intent(event_data['intent'])
                            root.finish(status='waiting' if item['event'].startswith('waiting')
                                        else 'completed', output=event_data)
                        yield item
        return wrapped
    return decorate


def trace_knowledge(function):
    @wraps(function)
    async def wrapped(runtime, query, *args, context, **kwargs):
        if current_request() is not None:
            return await function(runtime, query, *args, context=context, **kwargs)
        ctx = RequestTraceContext(conversation_id=context.conversation_id,
                                  entry_point=context.entry_point, turn_id=uuid4().hex)
        with get_observation_runtime().request(ctx, input={
            'question': context.original_question}) as root:
            root.set_intent('knowledge')
            result = await function(runtime, query, *args, context=context, **kwargs)
            root.finish(status='completed', output={'answer': result.answer,
                                                   'refused': result.refused})
            return result
    return wrapped


def trace_legacy(entry_point, *, stream=False):
    """Wrap the legacy boundaries without opening database sessions for Ch01."""
    def decorate(function):
        from inspect import signature
        sig = signature(function)

        def request(args, kwargs):
            params = sig.bind(*args, **kwargs).arguments
            ctx = RequestTraceContext(session_id=params.get('session_id'),
                user_id=params.get('user_id'), turn_id=uuid4().hex, entry_point=entry_point)
            return get_observation_runtime().request(ctx, input={
                key: params[key] for key in ('message', 'description', 'session_id', 'user_id')
                if key in params})

        if stream:
            @wraps(function)
            async def wrapped(*args, **kwargs):
                with request(args, kwargs) as root:
                    async with aclosing(function(*args, **kwargs)) as events:
                        async for item in events:
                            yield item
                    root.finish(status='completed', output={})
        else:
            @wraps(function)
            async def wrapped(*args, **kwargs):
                with request(args, kwargs) as root:
                    result = await function(*args, **kwargs)
                    root.finish(status='completed', output={'result': result})
                    return result
        return wrapped
    return decorate
