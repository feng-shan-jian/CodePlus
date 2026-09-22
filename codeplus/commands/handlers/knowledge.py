"""Thin development commands; the optional installed adapter owns the feature."""
from codeplus.commands.registry import Command, CommandType


async def handle_knowledge(ctx):
    parts = ctx.args.split(None, 1)
    action, value = (parts[0] if parts else ''), (parts[1].strip() if len(parts) > 1 else '')
    if getattr(ctx.ui, 'knowledge_feature_available', False) is not True:
        ctx.ui.add_system_message('knowledge feature_not_available in this entrypoint')
        return
    if action == 'off':
        ctx.ui.knowledge_library = None
        ctx.ui.add_system_message('Knowledge library selection cleared.')
    elif action == 'use' and value:
        try:
            from uuid import UUID
            value = str(UUID(value))
        except ValueError:
            ctx.ui.add_system_message('A library UUID is required.')
            return
        ctx.ui.knowledge_library = value
        ctx.ui.add_system_message('Knowledge library selected: '+value+'. Use /knowledge ask to query it.')
    elif action == 'ask' and value:
        if not ctx.ui.knowledge_library:
            ctx.ui.add_system_message('Select a library with /knowledge use <id> first.')
            return
        ctx.ui.send_knowledge_message(value)
    else:
        ctx.ui.add_system_message('Usage: /knowledge use <id> | ask <question> | off')


KNOWLEDGE_COMMAND = Command(name='knowledge', description='Knowledge development queries',
    usage='/knowledge use <id> | ask <question> | off', type=CommandType.LOCAL,
    handler=handle_knowledge)
