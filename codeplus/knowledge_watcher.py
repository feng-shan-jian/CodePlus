"""Host lifecycle for optional knowledge-source subscriptions."""

import asyncio


def start_knowledge_watcher(ui):
    path = getattr(ui, 'knowledge_development_config', '')
    if not path or getattr(ui, '_knowledge_watcher', None) is not None:
        return
    try:
        from agentic_rag.adapters.codeplus.management import load_settings
        from agentic_rag.ingestion.watching import SourceWatcher
        settings = load_settings(path)
        loop = asyncio.get_running_loop()

        def deliver(result):
            ui.last_knowledge_sync = result
            summary = result.get('data', {}).get('summary', {})
            if result['status'] == 'completed':
                message = (f"Knowledge auto-sync: {summary.get('published_new',0)} new, "
                           f"{summary.get('published_updated',0)} updated [{result['kb_id']}].")
            else:
                message = f"Knowledge auto-sync {result['status']} [{result['kb_id']}]; see /knowledge watch and /knowledge status."
            ui.add_system_message(message)

        def notify(result):
            if not loop.is_closed():
                loop.call_soon_threadsafe(deliver, result)

        ui._knowledge_watcher = SourceWatcher(settings, notify=notify).start()
    except (ImportError, ValueError, OSError) as error:
        ui.add_system_message(f'Knowledge auto-sync unavailable: {error}')


async def stop_knowledge_watcher(ui):
    watcher = getattr(ui, '_knowledge_watcher', None)
    if watcher is not None:
        watcher.stop()
        await asyncio.to_thread(watcher.join)
        ui._knowledge_watcher = None
