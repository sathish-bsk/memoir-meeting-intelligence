"""videoclip-common — Shared library for VideoClip/Memoir platform services."""

__version__ = "0.1.0"

from videoclip_common.auth import UserContext, get_current_user, decode_jwt
from videoclip_common.permissions import require_role
from videoclip_common.service_auth import verify_internal_call
from videoclip_common.storage import create_storage
from videoclip_common.events import RedisEventPublisher, RedisEventConsumer
from videoclip_common.config import BaseServiceSettings
from videoclip_common.middleware import RequestIDMiddleware

__all__ = [
    "UserContext",
    "get_current_user",
    "decode_jwt",
    "require_role",
    "verify_internal_call",
    "create_storage",
    "RedisEventPublisher",
    "RedisEventConsumer",
    "BaseServiceSettings",
    "RequestIDMiddleware",
]
