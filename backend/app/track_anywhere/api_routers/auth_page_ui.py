from __future__ import annotations

from html import escape

from fastapi.responses import HTMLResponse


def render_auth_page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} | Track Anywhere</title><style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f7f4ef;color:#16201d;font-family:Inter,ui-sans-serif,system-ui,sans-serif}}
.panel{{width:min(92vw,430px);display:grid;gap:18px}}.eyebrow{{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:#59645f}}
h1{{margin:0;font-size:32px;line-height:1.05}}form,.stack{{display:grid;gap:12px}}label{{display:grid;gap:6px;font-size:14px;color:#33413c}}
input,textarea{{box-sizing:border-box;width:100%;border:1px solid #c8d0ca;border-radius:8px;background:#fff;padding:11px 12px;font:inherit;color:#16201d}}
textarea{{min-height:140px;resize:vertical}}button,.secondary{{border:1px solid #16201d;border-radius:8px;background:#16201d;color:#fff;padding:11px 14px;font:inherit;text-align:center;text-decoration:none;cursor:pointer}}
.secondary{{background:#fff;color:#16201d}}.link{{color:#16201d}}.muted{{color:#59645f;line-height:1.5}}.error{{color:#a3332a}}
.scope-panel{{border:1px solid #d7ddd8;border-radius:8px;padding:14px;display:grid;gap:10px}}.scope-panel legend{{padding:0 6px;font-weight:650}}
.scope-list{{display:grid;gap:8px}}.scope-option{{display:flex;align-items:center;gap:9px}}.scope-option input{{width:auto;margin:0}}.scope-name{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}}.scope-group{{display:grid;gap:7px;border-top:1px solid #e2e6e2;padding-top:10px}}.scope-group-label{{font-weight:650}}.scope-group-items{{padding-left:23px}}
</style><script>
document.addEventListener("DOMContentLoaded",()=>{{const all=document.querySelector("[data-scope-all]");const boxes=[...document.querySelectorAll("input[name='approved_scope']")];const groups=[...document.querySelectorAll("[data-scope-group]")];if(!all||!boxes.length)return;const syncBox=(box,items)=>{{box.checked=items.every(item=>item.checked);box.indeterminate=!box.checked&&items.some(item=>item.checked)}};const sync=()=>{{syncBox(all,boxes);groups.forEach(group=>syncBox(group,boxes.filter(box=>box.dataset.scopeItem===group.dataset.scopeGroup)))}};all.addEventListener("change",()=>{{boxes.forEach(box=>box.checked=all.checked);sync()}});groups.forEach(group=>group.addEventListener("change",()=>{{boxes.filter(box=>box.dataset.scopeItem===group.dataset.scopeGroup).forEach(box=>box.checked=group.checked);sync()}}));boxes.forEach(box=>box.addEventListener("change",sync));sync()}});
</script></head><body>{body}</body></html>""", status_code=status_code)


def hidden_input(name: str, value: str | None) -> str:
    return f'<input type="hidden" name="{escape(name, quote=True)}" value="{escape(value or "", quote=True)}">'


def error_message(error: str | None) -> str:
    return f"<p class='error'>{escape(error)}</p>" if error else ""
