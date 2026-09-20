from __future__ import annotations

from rich.markup import escape
from textual.message import Message as TMessage
from textual.widgets import Static


class CompletionPopup(Static):

    DEFAULT_CSS = """
    CompletionPopup {
        height: auto;
        max-height: 9;
        display: none;
        padding: 0 1;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    """

    class Selected(TMessage):
        def __init__(self, value: str, input_text: str | None = None,
                     kind: str = "", submit: bool = False) -> None:
            super().__init__()
            self.value = value
            self.input_text = input_text
            self.kind = kind
            self.submit = submit

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._displays: list[str] = []
        self._values: list[str] = []
        self._cursor: int = 0
        self.input_text: str | None = None
        self.kind = ""
        self.hint = ""

    def show_pairs(self, pairs: list[tuple[str, str]], *, input_text: str | None = None,
                   kind: str = "", hint: str = "") -> None:
        """以 (display_text, value) 对的形式显示候选项。"""
        selected = self.get_selected() if self.input_text == input_text else None
        self._displays = [d for d, _ in pairs]
        self._values = [v for _, v in pairs]
        self._cursor = self._values.index(selected) if selected in self._values else 0
        self.input_text, self.kind, self.hint = input_text, kind, hint
        self._refresh_content()
        self.display = True

    def show(self, items: list[str], *, input_text: str | None = None) -> None:
        self.show_pairs([(escape(i), i) for i in items], input_text=input_text)

    def hide(self) -> None:
        self.display = False
        self._displays = []
        self._values = []
        self._cursor = 0
        self.input_text, self.kind, self.hint = None, "", ""

    @property
    def is_visible(self) -> bool:
        return bool(self.display)

    def move_up(self) -> None:
        if self._displays and self._cursor > 0:
            self._cursor -= 1
            self._refresh_content()

    def move_down(self) -> None:
        if self._displays and self._cursor < len(self._displays) - 1:
            self._cursor += 1
            self._refresh_content()

    def get_selected(self, input_text: str | None = None) -> str | None:
        # Changed is queued: a visible popup can still belong to an older edit.
        if input_text is not None and self.input_text != input_text:
            return None
        if not self._values:
            return None
        return self._values[self._cursor]

    def _refresh_content(self) -> None:
        lines = [f"[dim]{escape(' '.join(self.hint.split()))}[/]"] if self.hint else []
        start = max(0, self._cursor - 5)
        for i in range(start, min(start + 6, len(self._displays))):
            display = " ".join(self._displays[i].split())
            if i == self._cursor:
                lines.append(f"[bold reverse] {display} [/]")
            else:
                lines.append(f"  [dim]{display}[/]")
        self.update("\n".join(lines))

    def choose(self, input_text: str, *, submit: bool = False) -> bool:
        selected = self.get_selected(input_text)
        if selected is None:
            return False
        self.post_message(self.Selected(selected, input_text, self.kind, submit))
        self.hide()
        return True

    def on_click(self) -> None:
        selected = self.get_selected()
        if selected:
            self.post_message(self.Selected(selected, self.input_text, self.kind))
            self.hide()
