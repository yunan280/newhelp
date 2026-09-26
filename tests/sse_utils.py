"""SSE 响应体的解析工具 —— 测试专用。"""


def parse_sse(text: str) -> list[tuple[str, str]]:
    """把 SSE 文本解析成 [(event, data), ...]。

    只认 event: 和 data: 两种字段,够本项目的用例了。
    """
    events: list[tuple[str, str]] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = line[len("data:") :].strip()
        if event or data:
            events.append((event, data))
    return events
