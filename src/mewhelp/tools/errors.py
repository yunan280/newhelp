"""只为暂时网络故障重试；异常组的每个叶子都须是暂时故障。"""
import httpx


def classify_failure(exc: BaseException) -> tuple[str, bool]:
    if isinstance(exc, BaseExceptionGroup):
        leaves = [classify_failure(e) for e in exc.exceptions]
        return ('超时' if all(s == '超时' for s, _ in leaves) else '失败',
                bool(leaves) and all(retry for _, retry in leaves))
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return '超时', True
    if isinstance(exc, (ConnectionError, httpx.NetworkError, httpx.RemoteProtocolError)):
        return '失败', True
    if isinstance(exc, httpx.HTTPStatusError):
        return '失败', exc.response.status_code in {429, 502, 503, 504}
    return '失败', False
