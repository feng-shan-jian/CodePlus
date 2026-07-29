from __future__ import annotations

import pytest

from codeplus.app import CODEPLUS_ASCII_LOGO, CodePlusApp, ToolCallBlock, _format_detail


def test_startup_banner_uses_codeplus_wordmark_and_keeps_runtime_metadata():
    """启动横幅应展示完整字标，并在独立一行保留模型和工作目录。"""
    banner = CodePlusApp._make_banner("MiniMax-M3", "D:\\project")
    lines = banner.plain.splitlines()

    assert tuple(lines[:6]) == CODEPLUS_ASCII_LOGO
    assert lines[6] == "  CodePlus v0.1.0  ·  MiniMax-M3  ·  D:\\project"
    assert "/\\_/\\" not in banner.plain


@pytest.mark.asyncio
async def test_startup_banner_layout_has_room_for_complete_wordmark():
    """标题栏实际布局高度必须容纳六行字标和一行运行信息。"""
    app = CodePlusApp(providers=[])

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert app.query_one("#title-bar").size.height >= 7


def test_format_detail_colors_edit_file_diff_lines():
    output = (
        "Updated foo.py with 1 addition and 1 removal\n"
        "   10  unchanged\n"
        "-   11  old line\n"
        "+   11  new line"
    )
    detail = _format_detail("EditFile", {"file_path": "foo.py"}, output)

    assert "[green]" in detail
    assert "new line" in detail
    assert "[red]" in detail
    assert "old line" in detail
    assert "[dim]" in detail


def test_format_detail_escapes_brackets_in_code():
    # Python 类型标注里的方括号不应该被当成 Rich markup 标签解析
    output = "+    1  def foo(x: list[int]) -> dict[str, int]:"
    detail = _format_detail("EditFile", {"file_path": "foo.py"}, output)
    assert "list\\[int]" in detail
    assert "dict\\[str, int]" in detail


def test_edit_file_block_auto_expands_on_success():
    block = ToolCallBlock("EditFile", {"file_path": "foo.py"})
    block.set_result("Updated foo.py with 1 addition and 0 removals\n+    1  hello", False, 0.1)

    assert block._collapsed is False
    assert "hello" in block.render().plain


def test_edit_file_block_stays_collapsed_on_error():
    block = ToolCallBlock("EditFile", {"file_path": "foo.py"})
    block.set_result("Error: old_string not found in file", True, 0.1)
    assert block._collapsed is True


def test_other_tools_still_default_collapsed():
    block = ToolCallBlock("Bash", {"command": "ls"})
    block.set_result("file1\nfile2", False, 0.1)
    assert block._collapsed is True
