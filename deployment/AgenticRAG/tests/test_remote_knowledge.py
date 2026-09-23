"""Actual localhost WebSockets and host Agent; controlled answer/model/index IO."""
import asyncio
import json
from pathlib import Path
import runpy
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
import websockets

from codeplus.client import create_client, scoped_client
from codeplus.config import AppConfig, ProviderConfig
from codeplus.remote import RemoteServer
from codeplus.tools.base import StreamEnd, TextDelta
from agentic_rag.adapters.codeplus import policy as P

M = runpy.run_path(str(Path(__file__).with_name('test_run_modes.py')))
H = M['H']


async def receive(ws, kind, *, predicate=lambda data: True):
    async with asyncio.timeout(15):
        while True:
            message = json.loads(await ws.recv())
            if message['type'] == kind and predicate(message.get('data')):
                return message


@pytest.mark.parametrize('phase,operation', [('http', 'cancel'), ('http', 'disconnect'),
    ('permission', 'cancel'), ('permission', 'disconnect'), ('permission', 'allow')])
def test_websocket_owner_cancels_during_http_and_permissions(tmp_path, monkeypatch, phase, operation):
    async def run():
        monkeypatch.chdir(tmp_path)
        catalog, kb, base, rows, _ = M['R']['published'](tmp_path)
        settings = M['settings'](base, tmp_path)
        config_path = tmp_path/'development.json'; config_path.write_text(settings.model_dump_json())
        M['host_dependencies'](monkeypatch, settings, rows, [])
        provider = ProviderConfig('fixture', 'openai-compat', 'https://fixture.invalid', 'fixture', 'synthetic')
        pending_http, http_closed = asyncio.Event(), asyncio.Event()
        calls = []
        async def transport(request):
            body = json.loads(request.content); calls.append(body)
            if phase == 'http':
                pending_http.set()
                try:
                    await asyncio.Future()
                finally:
                    http_closed.set()
            if len(calls) == 1:
                response = H['sse_text'](calls=('search', 'knowledge_search', {'query': 'telescope certificate'}), terminal='tool_calls')
            else:
                source = next(m['content'] for m in body['messages'] if m['role'] == 'tool' and '<source ' in m['content'])
                item = json.JSONDecoder().raw_decode(source)[0]['items'][0]
                text = source.split('>\n', 1)[1].split('\n</source>', 1)[0]
                response = H['sse_text'](json.dumps({'markdown': '# Report\nVerified [^'+item['evidence_id']+']',
                    'citations': [{'evidence_id': item['evidence_id'], 'spans': item['returned_spans'], 'quotes': [text]}]}))
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=response)
        monkeypatch.setattr(P, 'scoped_client', lambda parent: scoped_client(parent, transport=httpx.MockTransport(transport)))
        config = AppConfig(providers=[provider], enable_fork=False, knowledge_development_config=str(config_path))
        server = RemoteServer([provider], config=config)
        server._init_agent()
        original_agent, original_conv = server.agent, server.conversation
        (tmp_path/'.codeplus/permissions.local.yaml').write_text('- rule: "WriteFile(*)"\n  effect: ask\n')
        # A normal task after the RAG cancellation must retain the ordinary loop.
        async def ordinary_stream(*a, **kw):
            yield TextDelta('ordinary after knowledge')
            yield StreamEnd('end_turn', 1, 1)
        seen = []
        busy_at_done = []
        broadcast = server._broadcast
        async def observed(message):
            seen.append(message)
            if message['type'] == 'command_done':
                busy_at_done.append(server._knowledge_active)
            await broadcast(message)
        monkeypatch.setattr(server, '_broadcast', observed)
        try:
            async with websockets.serve(server._ws_handler, '127.0.0.1', 0) as listener:
                uri = 'ws://127.0.0.1:'+str(listener.sockets[0].getsockname()[1])
                async with websockets.connect(uri) as owner, websockets.connect(uri) as observer:
                    await owner.send(json.dumps({'type': 'user_message', 'data': {'content': '/knowledge use '+str(kb)}}))
                    await receive(owner, 'command_done')
                    assert server.knowledge_library == str(kb), seen
                    begin = len(seen)
                    path = tmp_path/'report.md'
                    command = f'/knowledge report --output "{path}" Compare the certificates' if phase == 'permission' else '/knowledge ask telescope'
                    await owner.send(json.dumps({'type': 'user_message', 'data': {'content': command}}))
                    if phase == 'http':
                        await asyncio.wait_for(pending_http.wait(), 15)
                    else:
                        permission = await receive(owner, 'permission_request')
                    active = server._active_task
                    assert active is not None and not active.done()
                    assert not any(m['type'] in {'command_done', 'loop_complete'} for m in seen[begin:])
                    # The observer can disappear without cancelling another connection's task.
                    await observer.close(); await asyncio.sleep(.05)
                    assert server._active_task is active and not active.done()
                    if operation == 'allow':
                        await owner.send(json.dumps({'type': 'permission_response', 'data': {
                            'id': permission['data']['id'], 'response': 'allow'}}))
                    elif operation == 'disconnect':
                        await owner.close()
                    else:
                        await owner.send(json.dumps({'type': 'cancel', 'data': {}}))
                    await asyncio.wait_for(asyncio.shield(active), 15)
                    await asyncio.sleep(0)
                    outcome = server.last_knowledge_outcome
                    assert outcome.status == ('completed' if operation == 'allow' else 'cancelled'), seen
                    assert server.agent is original_agent and server.conversation is original_conv
                    assert not server._pending_perms and not server._knowledge_active and not server._command_running
                    assert catalog.get_pin(UUID(outcome.run_id)).state == 'released'
                    if phase == 'http':
                        assert http_closed.is_set()
                    else:
                        resolved = [m for m in seen if m['type'] == 'permission_resolved']
                        assert resolved and resolved[-1]['data']['reason'] == ('answered' if operation == 'allow' else 'cancelled')
                        assert path.exists() == (operation == 'allow')
                    if operation != 'allow':
                        assert not any(m['type'] == 'stream_text' for m in seen)
                    else:
                        complete = [m for m in seen[begin:] if m['type'] == 'loop_complete']
                        assert len(complete) == 1 and complete[0]['data']['totalTurns'] == 2
                    assert len([m for m in seen[begin:] if m['type'] == 'command_done']) == 1
                    assert busy_at_done[-1] is False
                monkeypatch.setattr(original_agent.client, 'stream', ordinary_stream)
                await server._handle_user_message('ordinary message')
                assert any(m == {'type': 'stream_end', 'data': {'text': 'ordinary after knowledge'}} for m in seen)
                assert original_agent.execution_policy is None
                saved_session = server.session.session_id
                saved_run = server.last_knowledge_run_id
                await server._handle_user_message('/session new')
                assert server.knowledge_library is None and server.last_knowledge_run_id is None
                await server._handle_user_message('/session resume '+saved_session)
                assert server.knowledge_library == str(kb) and server.last_knowledge_run_id == saved_run
                assert server.agent.execution_policy is None
        finally:
            server._cancel_active()
            await asyncio.gather(*tuple(server._message_tasks), return_exceptions=True)
            await server._flush_ui_messages()
            server.session.close()
            await original_agent.client._client.close()
    asyncio.run(run())


def test_remote_missing_config_is_actionable_and_never_plain_chat(tmp_path, monkeypatch):
    async def run():
        monkeypatch.chdir(tmp_path)
        provider = ProviderConfig('fixture', 'openai-compat', 'https://fixture.invalid', 'fixture', 'synthetic')
        server = RemoteServer([provider], config=AppConfig(providers=[provider]))
        server._init_agent(); server.knowledge_library = 'invalid'
        chat = AsyncMock(); monkeypatch.setattr(server.agent, 'run', chat)
        messages = []; monkeypatch.setattr(server, '_broadcast', AsyncMock(side_effect=messages.append))
        try:
            await server._handle_user_message('/knowledge ask question')
            assert not chat.called
            assert any('unavailable or invalid' in str(m) for m in messages)
        finally:
            await server._flush_ui_messages(); server.session.close(); await server.agent.client._client.close()
    asyncio.run(run())


def test_permission_event_is_consumed_by_existing_web_handler():
    from codeplus.web_content import INDEX_HTML
    assert "case 'permission_resolved'" in INDEX_HTML
    assert "getElementById('perm-' + msg.data.id)" in INDEX_HTML


@pytest.mark.parametrize('action', ['ask', 'report', 'continue'])
@pytest.mark.parametrize('body', ['/knowledge status', '/session new'])
def test_websocket_knowledge_body_is_not_redispatched(tmp_path, monkeypatch, action, body):
    async def run():
        monkeypatch.chdir(tmp_path)
        catalog, kb, base, rows, _ = M['R']['published'](tmp_path)
        settings = M['settings'](base, tmp_path)
        config_path = tmp_path/'development.json'; config_path.write_text(settings.model_dump_json())
        M['host_dependencies'](monkeypatch, settings, rows, [])
        provider = ProviderConfig('fixture', 'openai-compat', 'https://fixture.invalid', 'fixture', 'synthetic')
        requests = []
        async def transport(request):
            value = json.loads(request.content); requests.append(value)
            source = next((m['content'] for m in value['messages'] if m['role'] == 'tool' and '<source ' in str(m['content'])), None)
            if source is None:
                response = H['sse_text'](calls=('search', 'knowledge_search', {'query': 'telescope certificate'}), terminal='tool_calls')
            else:
                item = json.JSONDecoder().raw_decode(source)[0]['items'][0]
                text = source.split('>\n', 1)[1].split('\n</source>', 1)[0]
                response = H['sse_text'](json.dumps({'markdown': 'Verified [^'+item['evidence_id']+']',
                    'citations': [{'evidence_id': item['evidence_id'], 'spans': item['returned_spans'], 'quotes': [text]}]}))
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=response)
        monkeypatch.setattr(P, 'scoped_client', lambda parent: scoped_client(parent, transport=httpx.MockTransport(transport)))
        server = RemoteServer([provider], config=AppConfig(providers=[provider], enable_fork=False,
            knowledge_development_config=str(config_path)))
        server._init_agent()
        original_session = server.session.session_id
        events = []
        async def command(ws, text):
            begin = len(events)
            await ws.send(json.dumps({'type': 'user_message', 'data': {'content': text}}))
            async with asyncio.timeout(20):
                while True:
                    event = json.loads(await ws.recv()); events.append(event)
                    if event['type'] == 'permission_request':
                        await ws.send(json.dumps({'type': 'permission_response', 'data': {
                            'id': event['data']['id'], 'response': 'allow'}}))
                    if event['type'] == 'command_done':
                        return events[begin:]
        try:
            async with websockets.serve(server._ws_handler, '127.0.0.1', 0) as listener:
                uri = 'ws://127.0.0.1:'+str(listener.sockets[0].getsockname()[1])
                async with websockets.connect(uri) as ws:
                    await command(ws, '/knowledge use '+str(kb))
                    parent = None
                    if action == 'continue':
                        await command(ws, '/knowledge ask Initial certificate research')
                        assert server.last_knowledge_outcome.status == 'completed'
                        parent = server.last_knowledge_run_id
                    path = tmp_path/'slash-report.md'
                    options = ' --output "'+str(path)+'"' if action == 'report' else ' --run '+parent if parent else ''
                    before = len(requests)
                    actual = await command(ws, '/knowledge '+action+options+' '+body)
                    outcome = server.last_knowledge_outcome
                    assert outcome and outcome.status == 'completed' and outcome.run_id != parent
                    assert any(m['role'] == 'user' and m['content'] == body for request in requests[before:] for m in request['messages'])
                    assert server.session.session_id == original_session and server.knowledge_library == str(kb)
                    assert len([e for e in actual if e['type'] == 'loop_complete']) == 1
                    assert not server._knowledge_active and not server._command_running
                    run = catalog.get_run(UUID(outcome.run_id))
                    assert run.kb_id == kb and run.revision_id == catalog.get_library(kb).current_revision_id
                    assert (str(run.parent_run_id) == parent) if parent else run.parent_run_id is None
                    assert catalog.get_pin(run.run_id).state == 'released'
                    if action == 'report':
                        assert outcome.save.status == 'saved' and path.is_file()
                    with catalog._db.transaction() as db:
                        assert db.execute('SELECT count(*) FROM runs').fetchone()[0] == (2 if parent else 1)
                    # Top-level ordinary slash commands still dispatch.
                    await command(ws, '/session new')
                    assert server.session.session_id != original_session and server.knowledge_library is None
        finally:
            server._cancel_active()
            await asyncio.gather(*tuple(server._message_tasks), return_exceptions=True)
            await server._flush_ui_messages(); server.session.close(); await server.agent.client._client.close()
    asyncio.run(run())
