from __future__ import annotations

from collections.abc import Iterable
from html import escape

from ..errors import ValidationError
from ..platform_auth_models import DEFAULT_PLATFORM_SCOPE, parse_requested_scopes
from ..service_auth import AGENT_ALLOWED_SCOPES


def scope_controls(scope_text: str, available_scope_text: str | None = None) -> str:
    try:
        requested_scopes = parse_requested_scopes(scope_text)
        scopes = parse_requested_scopes(available_scope_text) if available_scope_text is not None else requested_scopes
    except ValidationError:
        escaped = escape(scope_text)
        return f"""
          <section class="scope-panel">
            <h2>Token permissions</h2>
            <p class="error">This request contains invalid scopes.</p>
            <code>{escaped}</code>
          </section>
        """
    requested_set = set(requested_scopes)
    options = "\n".join(
        f"""
          <label class="scope-option">
            <input type="checkbox" name="approved_scope" value="{escape(scope, quote=True)}" {"checked" if scope in requested_set else ""}>
            <span class="scope-name">{escape(scope)}</span>
          </label>
        """
        for scope in scopes
    )
    return f"""
      <fieldset class="scope-panel">
        <legend>Token permissions</legend>
        <p class="muted">The CLI default request is preselected. Add or remove permissions for this token.</p>
        <label class="scope-option scope-all">
          <input type="checkbox" data-scope-all>
          <span>All available permissions</span>
        </label>
        <div class="scope-list">{options}</div>
      </fieldset>
    """


def approved_scope_text(*, requested_scope_text: str, approved_scopes: list[str] | None, selection_present: bool) -> str:
    if not selection_present:
        return requested_scope_text
    selected = list(dict.fromkeys(approved_scopes or []))
    if not selected:
        raise ValidationError("select at least one scope")
    parse_requested_scopes(" ".join(selected))
    return " ".join(selected)


def requested_scope_text(value: str | None) -> str:
    return value or DEFAULT_PLATFORM_SCOPE


def actor_available_scope_text(actor_scopes: Iterable[str]) -> str:
    return " ".join(sorted(set(actor_scopes) & AGENT_ALLOWED_SCOPES))
