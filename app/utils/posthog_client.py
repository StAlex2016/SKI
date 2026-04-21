"""PostHog client wrapper — graceful degradation if not configured."""
import os
import logging

logger = logging.getLogger(__name__)

_enabled = False
_client = None

def _init():
    """Initialize PostHog lazily from env vars. Safe to call multiple times."""
    global _enabled, _client
    if _client is not None:
        return
    api_key = os.getenv("POSTHOG_API_KEY", "").strip()
    if not api_key:
        return
    try:
        import posthog
        posthog.api_key = api_key
        posthog.host = os.getenv("POSTHOG_HOST", "https://eu.posthog.com").strip()
        # Disable session recording, just events
        posthog.disabled = False
        _client = posthog
        _enabled = True
        logger.info(f"PostHog initialized: host={posthog.host}")
    except Exception as e:
        logger.warning(f"PostHog init failed: {e}")


def _env_tag() -> str:
    return os.getenv("APP_ENV", "prod").strip().lower() or "prod"


def capture(distinct_id, event: str, properties: dict = None):
    """Send a single event to PostHog. Silent on any failure.

    Every event is tagged with `env` (prod/staging) so staging traffic
    can be filtered out of product dashboards.
    """
    _init()
    if not _enabled or _client is None:
        return
    try:
        props = dict(properties or {})
        props.setdefault("env", _env_tag())
        _client.capture(
            distinct_id=str(distinct_id) if distinct_id is not None else "system",
            event=event,
            properties=props,
        )
    except Exception as e:
        logger.debug(f"PostHog capture failed: {e}")


def identify(distinct_id, properties: dict = None):
    """Attach/update user traits in PostHog. Silent on failure."""
    _init()
    if not _enabled or _client is None:
        return
    try:
        props = dict(properties or {})
        props.setdefault("env", _env_tag())
        _client.identify(
            distinct_id=str(distinct_id),
            properties=props,
        )
    except Exception as e:
        logger.debug(f"PostHog identify failed: {e}")


def is_enabled() -> bool:
    _init()
    return _enabled
