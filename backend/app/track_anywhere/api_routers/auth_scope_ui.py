from __future__ import annotations

from html import escape

from ..errors import ValidationError
from ..platform_auth_models import DEFAULT_PLATFORM_SCOPE, parse_requested_scopes


def scope_controls(scope_text: str) -> str:
    try:
        scopes = parse_requested_scopes(scope_text)
    except ValidationError:
        escaped = escape(scope_text)
        return f"""
          <section class="scope-panel">
            <h2>Requested access</h2>
            <p class="error">This request contains invalid scopes.</p>
            <code>{escaped}</code>
          </section>
        """
    options = "\n".join(
        f"""
          <label class="scope-option">
            <input type="checkbox" name="approved_scope" value="{escape(scope, quote=True)}" checked>
            <span class="scope-name">{escape(scope)}</span>
          </label>
        """
        for scope in scopes
    )
    return f"""
      <fieldset class="scope-panel">
        <legend>Requested access</legend>
        <p class="muted">Uncheck anything this CLI session should not receive.</p>
        <div class="scope-list">{options}</div>
      </fieldset>
    """


def approved_scope_text(*, requested_scope_text: str, approved_scopes: list[str] | None, selection_present: bool) -> str:
    if not selection_present:
        return requested_scope_text
    requested_scopes = parse_requested_scopes(requested_scope_text)
    selected = list(dict.fromkeys(approved_scopes or []))
    if not selected:
        raise ValidationError("select at least one scope")
    unexpected = set(selected) - set(requested_scopes)
    if unexpected:
        raise ValidationError(f"approved scopes were not requested: {sorted(unexpected)}")
    selected_set = set(selected)
    return " ".join(scope for scope in requested_scopes if scope in selected_set)


def requested_scope_text(value: str | None) -> str:
    return value or DEFAULT_PLATFORM_SCOPE
