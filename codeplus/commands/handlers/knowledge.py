"""Thin development commands; the optional installed adapter owns the feature."""
import shlex

from codeplus.commands.registry import Command, CommandType


USAGE = '/knowledge use <id> | ask [--mode fixed|auto] <question> | report --output <path> [--mode fixed|auto] <task> | continue [--run <id>] [--output <new-path>] [--mode fixed|auto] [request] | off'


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
        mode = None
        if value.startswith('--mode'):
            selection = value.split(None, 2)
            if len(selection) != 3 or selection[0] != '--mode' or selection[1] not in {'fixed','auto'}:
                ctx.ui.add_system_message('Usage: /knowledge ask [--mode fixed|auto] <question>')
                return
            _, mode, value = selection
        if not ctx.ui.knowledge_library:
            ctx.ui.add_system_message('Select a library with /knowledge use <id> first.')
            return
        if mode is None:
            ctx.ui.send_knowledge_message(value)
        else:
            ctx.ui.send_knowledge_message(value, mode=mode)
    elif action in {'report','continue'}:
        if not ctx.ui.knowledge_library:
            ctx.ui.add_system_message('Select a library with /knowledge use <id> first.')
            return
        try:
            tokens = shlex.split(value, posix=False)
            options = {}
            while tokens and tokens[0].startswith('--'):
                key = tokens.pop(0)
                if key not in {'--mode','--output','--run'} or not tokens or key in options:
                    raise ValueError('invalid option')
                options[key] = tokens.pop(0).strip('\"\'')
            mode, target, parent = (options.get(key) for key in ('--mode','--output','--run'))
            if mode is not None and mode not in {'fixed','auto'}:
                raise ValueError('invalid mode')
            if action == 'report' and (not target or not tokens or parent):
                raise ValueError('report needs output and task')
            if action == 'continue':
                parent = parent or getattr(getattr(ctx.ui, 'last_knowledge_outcome', None), 'run_id', None)
                if not parent:
                    ctx.ui.add_system_message('No research run to continue. Use --run <id>, or start a new task with its goal and constraints.')
                    return
                from uuid import UUID
                parent = str(UUID(parent))
            request = ' '.join(tokens) or 'Continue the previous research, prioritizing unresolved questions.'
        except ValueError:
            ctx.ui.add_system_message('Usage: '+USAGE)
            return
        ctx.ui.send_knowledge_message(request, mode=mode, task_kind='report' if target else 'qa',
                                      report_path=target, parent_run_id=parent)
    else:
        ctx.ui.add_system_message('Usage: '+USAGE)


KNOWLEDGE_COMMAND = Command(name='knowledge', description='Knowledge development queries',
    usage=USAGE, type=CommandType.LOCAL,
    handler=handle_knowledge)
