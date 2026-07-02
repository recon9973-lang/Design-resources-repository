"""공용 HTTP GET — 재시도(지수 백오프) + User-Agent. 표준 라이브러리만 사용.

공공 API는 간헐적으로 느리거나 5xx를 반환하므로 모든 커넥터가 이 헬퍼를 쓴다.
"""

import time
import urllib.error
import urllib.request

USER_AGENT = "medirank-local/0.1 (+stage0-validation)"


def get_bytes(url: str, headers: dict | None = None,
              timeout: int = 60, retries: int = 3, backoff: float = 2.0) -> bytes:
    """GET 요청. 실패 시 retries회까지 backoff^n초 간격으로 재시도."""
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
            # 4xx는 재시도해도 같으므로 즉시 전파 (429 제외)
            if isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500 and e.code != 429:
                raise
            last_err = e
            if attempt < retries:
                time.sleep(backoff ** (attempt + 1))
    raise last_err  # type: ignore[misc]
