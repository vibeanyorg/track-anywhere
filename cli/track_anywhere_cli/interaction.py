from __future__ import annotations

from dataclasses import dataclass
import webbrowser
from typing import Protocol

import click


class Interaction(Protocol):
    def open_url(self, url: str) -> None:
        ...

    def prompt(self, text: str, *, secret: bool = False) -> str:
        ...


@dataclass(frozen=True)
class ClickInteraction:
    open_browser: bool = True

    def open_url(self, url: str) -> None:
        if not self.open_browser:
            return
        webbrowser.open(url)

    def prompt(self, text: str, *, secret: bool = False) -> str:
        return click.prompt(text, hide_input=secret, err=True)
