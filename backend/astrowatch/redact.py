"""로그 비밀값 차단 (NF3001) — 설정된 키 · 토큰 값이 로그에 나오면 *** 로 바꾼다.

예외 메시지 · 트레이스백에 요청 URL(쿼리스트링의 serviceKey 등)이 섞여도 키가 남지 않도록
레코드의 메시지 · 인자 · 예외 텍스트를 모두 치환한다. app 시작 시 install_redaction() 을 부른다.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from typing import Iterable

MASK = "***"
MIN_SECRET_LEN = 6   # 너무 짧은 값은 일반 단어를 가릴 수 있어 제외
_TOKEN_FIELDS = ("token", "refresh_token", "client_secret")
_LOGGER_NAMES = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "fastapi")


def _variants(value: str) -> set[str]:
    """원본 · URL 인코딩 · 디코딩 형태 모두 (data.go.kr 키는 두 형태가 섞여 다닌다)."""
    out = {value, urllib.parse.unquote(value), urllib.parse.quote(value, safe=""), urllib.parse.quote_plus(value)}
    return {v for v in out if v and len(v) >= MIN_SECRET_LEN}


def _token_file_secrets(path: object) -> set[str]:
    found: set[str] = set()
    try:
        from pathlib import Path

        p = Path(str(path))
        if not p.is_file() or p.is_symlink():
            return found
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return found
    blocks = [data] + [v for v in data.values() if isinstance(v, dict)] if isinstance(data, dict) else []
    for block in blocks:
        for key in _TOKEN_FIELDS:
            v = block.get(key)
            if isinstance(v, str):
                found.add(v)
    return found


def collect_secrets() -> set[str]:
    """현재 설정에서 가릴 값 목록. 값 자체는 어디에도 출력하지 않는다."""
    import os

    from astrowatch.config import get_settings

    s = get_settings()
    raw: set[str] = set()
    for v in (s.gms_key, s.data_go_kr_key, os.environ.get("DATA_GO_KR_KEY"), s.kakao_rest_key, s.openweather_key,
              s.app_access_token):
        if v and v.strip():
            raw.add(v.strip())
    for f in (s.google_token_file, s.google_client_secret_file):
        if f:
            raw |= _token_file_secrets(f)
    out: set[str] = set()
    for v in raw:
        out |= _variants(v)
    return out


class SecretRedactingFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__("astrowatch.redact")
        self.update(secrets)

    def update(self, secrets: Iterable[str]) -> None:
        # 긴 값부터 치환해야 부분 문자열이 먼저 바뀌어 일부가 남는 일이 없다
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def _scrub(self, text: str) -> str:
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, MASK)
        return text

    def _scrub_value(self, value: object) -> object:
        """인자 하나를 치환. 문자열이 아니어도 str() 에 비밀값이 있으면 치환한 문자열로 바꾼다."""
        if isinstance(value, str):
            return self._scrub(value)
        try:
            text = str(value)
        except Exception:  # noqa: BLE001
            return value
        return self._scrub(text) if any(s in text for s in self._secrets) else value

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        # msg 와 args 를 합치지 않고 각각 치환한다 — uvicorn 접근 로그 포맷터는 args 를
        # 5개 튜플로 풀어 쓰므로 구조(튜플/딕셔너리)를 그대로 유지해야 한다.
        if isinstance(record.msg, str):
            record.msg = self._scrub(record.msg)
        else:
            record.msg = self._scrub_value(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._scrub_value(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._scrub_value(v) for v in record.args)
            else:
                record.args = self._scrub_value(record.args)
        if record.exc_info and record.exc_info[1] is not None:
            # 트레이스백 텍스트를 미리 만들어 치환해 둔다 (Formatter 는 exc_text 가 있으면 재사용)
            text = logging.Formatter().formatException(record.exc_info)
            record.exc_text = self._scrub(text)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = self._scrub(record.exc_text)
        if record.stack_info:
            record.stack_info = self._scrub(record.stack_info)
        return True


_filter: SecretRedactingFilter | None = None


def install_redaction(secrets: Iterable[str] | None = None) -> SecretRedactingFilter:
    """루트 · uvicorn 로거와 그 핸들러에 필터를 설치한다. 여러 번 불러도 하나만 붙는다."""
    global _filter
    values = collect_secrets() if secrets is None else set(secrets)
    if _filter is None:
        _filter = SecretRedactingFilter(values)
    else:
        _filter.update(values)
    for name in _LOGGER_NAMES:
        lg = logging.getLogger(name)
        if _filter not in lg.filters:
            lg.addFilter(_filter)
        for h in lg.handlers:
            if _filter not in h.filters:
                h.addFilter(_filter)
    return _filter
