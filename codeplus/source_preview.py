"""Message-local citation links and an inline, read-only source preview."""

import re
from urllib.parse import unquote

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, Markdown, OptionList, Static

from codeplus.knowledge.citations import SOURCE_LINK, citation_link


class CitationMarkdown(Markdown):
    BINDINGS = [Binding("enter", "sources", "查看来源")]
    DEFAULT_CSS = """
    CitationMarkdown:focus { border-left: tall $accent; }
    """

    def __init__(self, presentation, knowledge, **kwargs):
        self.references = presentation["citations"]
        self.knowledge = knowledge
        text = presentation["display_text"]
        if self.references:
            # Escape document names as Markdown text; names are untrusted data.
            links = []
            for ref in self.references:
                label = re.sub(r"([\\`*_{}\[\]()<>!#|])", r"\\\1", ref["label"].replace("&", "&amp;"))
                label = label.replace("\n", " ").replace("\r", " ")
                links.append(f"[\\[{ref['number']}\\] {label}]({citation_link(ref['citation_id'])})")
            text += "\n\n" + "  \n".join(links) + "\n\n*Tab 聚焦回答，Enter 查看来源；Esc 关闭预览。*"
        super().__init__(text, open_links=False, **kwargs)
        self.can_focus = bool(self.references)

    async def on_markdown_link_clicked(self, event: Markdown.LinkClicked):
        event.stop()
        if event.href.startswith(SOURCE_LINK):
            await self.open_preview(unquote(event.href[len(SOURCE_LINK):]))
        else:
            self.app.open_url(event.href)

    async def action_sources(self):
        if self.references:
            await self.open_preview(self.references[0]["citation_id"])

    async def open_preview(self, citation_id):
        for previous in self.app.query(InlineSourcePreview):
            await previous.remove()
        preview = InlineSourcePreview(self.references, citation_id, self.knowledge, self.app.focused or self)
        await self.parent.mount(preview, after=self)
        preview.scroll_visible()


class InlineSourcePreview(Vertical):
    BINDINGS = [Binding("escape", "close", "关闭来源", priority=True)]
    DEFAULT_CSS = """
    InlineSourcePreview { height: auto; margin: 1 2; padding: 1; border: round $accent; }
    InlineSourcePreview OptionList { height: auto; max-height: 6; }
    InlineSourcePreview .source-details { height: auto; margin: 1 0; }
    InlineSourcePreview VerticalScroll { height: auto; max-height: 16; }
    InlineSourcePreview Button { margin-top: 1; }
    """

    def __init__(self, references, citation_id, knowledge, return_focus):
        super().__init__()
        self.references = references
        self.citation_id = citation_id
        self.knowledge = knowledge
        self.return_focus = return_focus

    def compose(self) -> ComposeResult:
        yield Static("引用来源 · ↑↓ 选择，Enter 打开，Esc 关闭", markup=False)
        yield OptionList(*(Text(f"[{ref['number']}] {ref['label']}") for ref in self.references))
        yield Static("正在读取来源…", classes="source-details", markup=False)
        with VerticalScroll():
            yield Static("", classes="source-text", markup=False)
        yield Button("关闭来源 (Esc)", classes="source-close")

    async def on_mount(self):
        options = self.query_one(OptionList)
        options.highlighted = next((i for i, ref in enumerate(self.references)
                                    if ref["citation_id"] == self.citation_id), 0)
        options.focus()
        await self.show_source(self.citation_id)

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        event.stop()
        await self.show_source(self.references[event.option_index]["citation_id"])

    async def show_source(self, citation_id):
        try:
            source = await self.knowledge.preview(citation_id)
            details = (f"{source['filename']} · {source['location']}\n{source['status_label']}\n"
                       f"版本: {source['generation_id']}\n{source['citation_id']}")
            text = source["text"]
        except Exception as exc:
            details, text = f"无法打开引用 {citation_id}\n{exc}", ""
        if self.is_attached:
            self.query_one(".source-details", Static).update(details)
            self.query_one(".source-text", Static).update(text)
            self.query_one(VerticalScroll).scroll_home(animate=False)

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        self.action_close()

    def action_close(self):
        self.remove()
        if self.return_focus.is_mounted:
            self.return_focus.focus()
