from dataclasses import asdict, dataclass
from typing import Literal


@dataclass(frozen=True)
class RequestTraceContext:
    session_id: str | None = None
    user_id: str | None = None
    conversation_id: int | None = None
    turn_id: str | None = None
    entry_point: str = 'agent'
    trace_kind: Literal['chat', 'flywheel', 'evaluation'] = 'chat'
    origin_trace_id: str | None = None

    def metadata(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value is not None}
