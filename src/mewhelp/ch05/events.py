def event(event_type: str, **data) -> dict:
    return {"event": event_type, "data": data}
