"""Reports and continuation use the ordinary Agent tools and permissions."""
import asyncio
from contextlib import aclosing
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from codeplus.agent import Agent, StreamText
from codeplus.client import create_client, scoped_client
from codeplus.config import ProviderConfig
from codeplus.conversation import ConversationManager
from codeplus.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine
from codeplus.tools import create_default_registry
from agentic_rag.adapters.codeplus.policy import KnowledgePolicy

M = runpy.run_path(str(Path(__file__).with_name('test_run_modes.py')))


@pytest.mark.parametrize('completion', [False, True])
@pytest.mark.parametrize('effect', ['allow', 'deny'])
def test_report_uses_normal_write_permission_and_continues(tmp_path, monkeypatch, completion, effect):
    async def run():
        catalog, kb, base, rows, _ = M['R']['published'](tmp_path)
        settings = M['settings'](base, tmp_path)
        M['host_dependencies'](monkeypatch, settings, rows, [])
        provider = ProviderConfig('fixture', 'openai-compat', 'https://fixture.invalid', 'fixture', 'synthetic')
        report_path = tmp_path/'report.md'
        report = '# Findings\n\nThe source describes a telescope.\n'
        observed = []

        def transport(request):
            body = json.loads(request.content)
            observed.append(body)
            names = {tool['function']['name'] for tool in body['tools']}
            assert {'WriteFile', 'ReadFile', 'knowledge_search', 'knowledge_open'} <= names
            step = len(observed)
            if step in (1, 4):
                content = M['H']['sse_text'](calls=('search'+str(step), 'knowledge_search', {'query':'telescope'}), terminal='tool_calls')
            elif step == 2:
                content = M['H']['sse_text'](calls=('write', 'WriteFile', {'file_path':str(report_path), 'content':report}), terminal='tool_calls')
            else:
                content = M['H']['sse_text']('The telescope finding remains unresolved.')
            return httpx.Response(200, headers={'content-type':'text/event-stream'}, content=content)

        parent = create_client(provider)
        client = scoped_client(parent, transport=httpx.MockTransport(transport))
        permissions = tmp_path/'permissions.yaml'
        permissions.write_text('- rule: "WriteFile(*)"\n  effect: '+effect+'\n', encoding='utf-8')
        checker = PermissionChecker(DangerousCommandDetector(), PathSandbox(str(tmp_path)),
            RuleEngine(project_rules_path=permissions), mode=PermissionMode.DEFAULT)
        registry = create_default_registry()

        async def execute(policy, prompt):
            agent = Agent(client, registry, 'openai-compat', work_dir=str(tmp_path),
                execution_policy=policy, permission_checker=checker)
            if completion:
                text = await agent.run_to_completion(prompt)
            else:
                conversation = ConversationManager()
                conversation.add_user_message(prompt)
                parts = []
                async with aclosing(agent.run(conversation)) as stream:
                    async for event in stream:
                        if isinstance(event, StreamText):
                            parts.append(event.text)
                text = ''.join(parts)
            return agent.last_run_outcome, text

        try:
            first, text = await execute(KnowledgePolicy(settings, kb, provider, task_kind='report',
                report_path=str(report_path)), 'Compare the sources and save a report.')
            assert 'telescope' in text
            assert report_path.exists() == (effect == 'allow')
            if effect == 'allow':
                assert report_path.read_text(encoding='utf-8') == report
                assert first.save.status == 'saved' and first.save.path == str(report_path)
            else:
                assert first.save is None
            assert registry.get('knowledge_search') is None
            second, _ = await execute(KnowledgePolicy(settings, kb, provider,
                parent_run_id=UUID(first.run_id)), 'Continue checking the unresolved finding.')
            assert second.status == 'completed'
            assert 'telescope finding remains unresolved' in json.dumps(observed[3]['messages'])
            run = catalog.get_run(UUID(second.run_id))
            assert str(run.parent_run_id) == first.run_id
            assert catalog.get_pin(run.run_id).state == 'released'
            assert len(second.research['rounds']) == 2
        finally:
            await client.aclose()
            await parent._client.close()
    asyncio.run(run())


def test_report_and_continue_commands_keep_quoted_windows_path():
    from codeplus.commands.handlers.knowledge import handle_knowledge
    calls, messages = [], []
    parent = '0ab29e6f-61bb-4282-a3dd-e67ac85f75d9'
    ui = SimpleNamespace(knowledge_feature_available=True, knowledge_library='kb',
        last_knowledge_outcome=SimpleNamespace(run_id=parent),
        send_knowledge_message=lambda *args, **kwargs:calls.append((args, kwargs)),
        add_system_message=messages.append)
    async def run():
        for args in ['report --output "D:\\Reports\\one report.md" --mode auto 比较两份材料', 'continue 补查缺口']:
            await handle_knowledge(SimpleNamespace(args=args, ui=ui))
    asyncio.run(run())
    assert not messages and len(calls) == 2
    assert calls[0][1]['report_path'] == 'D:\\Reports\\one report.md'
    assert calls[1][1]['parent_run_id'] == parent
