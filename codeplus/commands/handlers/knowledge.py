"""Shared TUI, prompt and Remote commands; lifecycle work lives in the adapter."""
import json
import shlex
from uuid import UUID

from codeplus.commands.registry import Command, CommandType


USAGE = '''/knowledge create <name> | use <library-id> | off
/knowledge import <path>... | reimport <document-id> <path> | remove <document-id>...
/knowledge watch [<path>...] | unwatch <path>... | sync
/knowledge status | sources [--revision <id>] | open <citation-id>
/knowledge retry <batch-id> [--accept-input-changes]
/knowledge recover [<batch-id>] [--choice continue|abandon] | abandon <batch-id>
/knowledge model [<proposal-id>] [--choice confirm|retry|keep_original]
/knowledge ask [--mode fixed|auto] <question>
/knowledge report --output <path> [--mode fixed|auto] <task>
/knowledge continue [--run <id>] [--output <new-path>] [--mode fixed|auto] [request]'''


def _unquote(value):
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'" else value


def parse_knowledge(args):
    """Windows paths retain backslashes; quoted paths/names may contain spaces."""
    tokens = [_unquote(v) for v in shlex.split(args, posix=False)]
    action = tokens.pop(0) if tokens else 'help'
    allowed = {'ask': {'mode'}, 'report': {'mode', 'output'}, 'continue': {'mode', 'output', 'run'},
               'sources': {'revision'}, 'retry': {'accept-input-changes'}, 'recover': {'choice'}, 'model': {'choice'}}
    actions = {'create', 'use', 'off', 'import', 'reimport', 'remove', 'status', 'sources', 'open', 'watch', 'unwatch', 'sync',
               'retry', 'recover', 'abandon', 'model', 'ask', 'report', 'continue', 'help'}
    if action not in actions:
        raise ValueError('Unknown knowledge command.\n'+USAGE)
    options, values = {}, []
    while tokens:
        token = tokens.pop(0)
        if token == '--':
            values.extend(tokens)
            break
        if not token.startswith('--'):
            values.append(token)
            if action in {'ask', 'report', 'continue'}:
                values.extend(tokens)
                break
            continue
        key = token[2:]
        if key not in allowed.get(action, ()) or key in options:
            raise ValueError('Unknown or repeated option: '+token)
        if key == 'accept-input-changes':
            options[key] = True
        else:
            if not tokens or tokens[0].startswith('--'):
                raise ValueError('Missing value for '+token)
            options[key] = tokens.pop(0)
    if 'choice' in options:
        options['choice'] = options['choice'].strip() or None
    if options.get('mode') not in {None, 'fixed', 'auto'}:
        raise ValueError('Mode must be fixed or auto.')
    if action == 'recover' and options.get('choice') not in {None, 'continue', 'abandon'}:
        raise ValueError('Recovery choice must be continue or abandon.')
    if action == 'model' and options.get('choice') not in {None, 'confirm', 'retry', 'keep_original'}:
        raise ValueError('Model choice must be confirm, retry or keep_original.')
    counts = {'use': (1, 1), 'off': (0, 0), 'import': (1, None), 'reimport': (2, 2), 'remove': (1, None),
              'watch': (0, None), 'unwatch': (1, None), 'sync': (0, 0),
              'status': (0, 0), 'sources': (0, 0), 'open': (1, 1), 'retry': (1, 1),
              'recover': (0, 1), 'abandon': (1, 1), 'model': (0, 1), 'help': (0, 0)}
    lo, hi = counts.get(action, (1 if action != 'continue' else 0, None))
    if len(values) < lo or hi is not None and len(values) > hi:
        raise ValueError('Missing or extra arguments.\n'+USAGE)
    if action == 'report' and not options.get('output'):
        raise ValueError('Report requires --output <path>.')
    if action in {'recover', 'model'} and options.get('choice') and not values:
        raise ValueError('Select the batch/proposal ID before making a choice.')
    identities = values if action in {'use', 'remove', 'open', 'retry', 'recover', 'abandon', 'model'} else values[:1] if action == 'reimport' else []
    for identity in [*identities, *(options[key] for key in ('revision', 'run') if key in options)]:
        try:
            UUID(identity)
        except ValueError as error:
            raise ValueError('A valid UUID is required: '+identity) from error
    if action == 'create':
        values = [' '.join(values)]
    return action, tuple(values), {key.replace('-', '_'): val for key, val in options.items()}


def exit_status(status):
    """Knowledge only; ordinary host task exit behavior remains unchanged."""
    return {'completed': 0, 'partial': 2, 'incomplete': 2, 'waiting_confirmation': 3, 'cancelled': 130}.get(status, 1)


def publish_result(ui, result):
    ui.last_knowledge_command = result
    if hasattr(ui, 'publish_knowledge_result'):
        ui.publish_knowledge_result(result)
    else:
        ui.add_system_message(json.dumps(result, ensure_ascii=False, indent=2))
    if result['status'] == 'waiting_confirmation':
        ui.add_system_message('Select the shown batch with /knowledge recover <id> --choice continue|abandon; '
            'for a model proposal use /knowledge model <id> --choice confirm|retry|keep_original. '
            'No work starts without an explicit choice.')


def save_selection(ui):
    session = getattr(ui, 'session', None)
    if session is not None:
        session.set_rag_selection(ui.knowledge_library, getattr(ui, 'last_knowledge_run_id', None))


async def handle_knowledge(ctx):
    ui = ctx.ui
    try:
        action, values, options = parse_knowledge(ctx.args)
        if action == 'help':
            ui.add_system_message(USAGE)
            return
        if getattr(ui, 'knowledge_feature_available', False) is not True:
            raise ValueError('knowledge feature_not_available in this entrypoint')
        if action == 'off':
            ui.knowledge_library = ui.last_knowledge_run_id = None
            ui.last_knowledge_outcome = None
            save_selection(ui)
            publish_result(ui, {'action': action, 'status': 'completed', 'stop_reason': 'finished', 'data': {'selected_library': None}})
            return
        if action in {'ask', 'report', 'continue'}:
            if not getattr(ui, 'knowledge_library', None):
                raise ValueError('Select a library with /knowledge use <id> first.')
            parent = options.get('run')
            if action == 'continue':
                parent = parent or getattr(getattr(ui, 'last_knowledge_outcome', None), 'run_id', None) or getattr(ui, 'last_knowledge_run_id', None)
                if not parent:
                    raise ValueError('No research run to continue. Use --run <id>, or start a task with its goal and constraints.')
            request = ' '.join(values) or 'Continue the previous research, prioritizing unresolved questions.'
            if action == 'ask':
                ui.send_knowledge_message(request, **({'mode': options['mode']} if options.get('mode') else {}))
            else:
                ui.send_knowledge_message(request, mode=options.get('mode'),
                    task_kind='report' if options.get('output') else 'qa', report_path=options.get('output'), parent_run_id=parent)
            return
        try:
            from agentic_rag.adapters.codeplus.management import execute_management
        except ImportError as error:
            raise ValueError('Install the local codeplus-agentic-rag development wheel into this host environment.') from error
        selected = values[0] if action == 'use' else getattr(ui, 'knowledge_library', None)
        ui._knowledge_active = True
        try:
            result = await execute_management(getattr(ui, 'knowledge_development_config', ''), action, selected,
                                               arguments=values, options=options)
        finally:
            ui._knowledge_active = False
        if result['status'] == 'completed' and action in {'create', 'use'}:
            ui.knowledge_library = result['data']['kb_id']
            ui.last_knowledge_run_id = ui.last_knowledge_outcome = None
            save_selection(ui)
        publish_result(ui, result)
    except (ValueError, OSError) as error:
        publish_result(ui, {'action': 'command', 'status': 'failed', 'stop_reason': 'invalid_input',
                            'data': {'message': str(error)}})
    except Exception as error:
        publish_result(ui, {'action': 'command', 'status': 'failed', 'stop_reason': 'explicit_error',
                            'data': {'message': type(error).__name__}})


KNOWLEDGE_COMMAND = Command(name='knowledge', description='Knowledge libraries, sources, questions and reports',
                            usage=USAGE, type=CommandType.LOCAL, handler=handle_knowledge)
