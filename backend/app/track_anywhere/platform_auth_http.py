from __future__ import annotations

import json
from urllib.parse import parse_qs

from .security import Actor


def identity_for_actor(actor: Actor, *, provider: str) -> dict[str, object]:
    name = "Local Owner" if actor.actor_id == "owner" else actor.actor_id
    return {
        "provider": provider,
        "subject": actor.actor_id,
        "email": None,
        "name": name,
        "role": actor.actor_type,
        "scopes": sorted(actor.scopes),
    }


def form_or_json_payload(content_type: str, body: bytes) -> dict[str, object]:
    if "application/json" in content_type:
        if not body:
            return {}
        parsed = json.loads(body.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    return {key: values[-1] for key, values in parse_qs(body.decode("utf-8")).items() if values}
