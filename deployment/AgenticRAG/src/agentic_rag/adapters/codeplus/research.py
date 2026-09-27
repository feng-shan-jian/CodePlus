"""Public research handoff notes and accounting over the existing run records."""
from __future__ import annotations

import json
from uuid import UUID

from ...domain import RagError


def history(catalog, run_id):
    """Each ancestor retains its own binding and nullable usage."""
    rounds = []
    while run_id is not None:
        run = catalog.get_run(UUID(str(run_id)))
        with catalog._db.transaction() as db:
            row = db.execute('SELECT detail FROM host_runs WHERE run_id=?', (str(run.run_id),)).fetchone()
        detail = json.loads(row[0]) if row and row[0] else {}
        rounds.append({'run_id':str(run.run_id), 'parent_run_id':str(run.parent_run_id) if run.parent_run_id else None,
            'kb_id':str(run.kb_id), 'revision_id':str(run.revision_id), 'status':run.status.value,
            'stop_reason':run.stop_reason, 'task_kind':run.resolved_config.task_kind,
            'budget':run.resolved_config.budget,
            'usage':run.usage.model_dump(),
            'save':detail.get('save')})
        run_id = run.parent_run_id
    rounds.reverse()
    total = {key:(sum(r['usage'][key] for r in rounds) if all(r['usage'][key] is not None for r in rounds) else None)
             for key in rounds[-1]['usage'] if key != 'schema_version'}
    return {'rounds':rounds, 'total_usage':total}


def continuation(catalog, kb_id, parent_run_id):
    try:
        parent = catalog.get_run(parent_run_id)
    except RagError as error:
        raise ValueError('Research run is missing. Start a new task with the goal and constraints.') from error
    if parent.kb_id != kb_id:
        raise ValueError('Research belongs to another library. Start a new task after changing libraries.')
    if parent.status.value == 'running':
        raise ValueError('Research is still running. Wait for it to stop before continuing.')
    with catalog._db.transaction() as db:
        row = db.execute('SELECT detail FROM host_runs WHERE run_id=?', (str(parent_run_id),)).fetchone()
    detail = json.loads(row[0]) if row and row[0] else {}
    progress = detail.get('progress')
    if not progress or not progress.get('goal'):
        raise ValueError('Research progress is missing. Start a new task with the original goal, constraints and gaps.')
    rounds = history(catalog, parent_run_id)['rounds']
    # These public notes are context only. No old source bodies, source handles
    # or evidence IDs enter the new registry or authorize citations.
    public_rounds = []
    with catalog._db.transaction() as db:
        for item in rounds:
            row = db.execute('SELECT detail FROM host_runs WHERE run_id=?', (item['run_id'],)).fetchone()
            notes = (json.loads(row[0]).get('progress') or {}) if row and row[0] else {}
            public_rounds.append({'run_id':item['run_id'], 'revision_id':item['revision_id'],
                'status':item['status'], 'stop_reason':item['stop_reason'],
                'public_answer':notes.get('public_answer') if not notes.get('notes_supplied', bool(notes.get('findings') or notes.get('covered'))) else None,
                **{key:notes.get(key,[]) for key in ('covered','pending','findings','revised','unverified','source_leads')}})
    return {'goal':progress['goal'], 'user_requests':progress.get('user_requests',[progress['goal']]),
            'historical_rounds':public_rounds,
            'parent_run_id':str(parent_run_id), 'previous_revision_id':str(parent.revision_id),
            'previous_status':parent.status.value, 'stop_reason':parent.stop_reason,
            'finding_status':'unverified_historical_leads'}
