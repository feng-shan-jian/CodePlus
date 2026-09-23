"""Compatibility surface for the original explicit Dense candidate API."""

from .search import RetrievalSearch


class DenseSearch(RetrievalSearch):
    def __init__(self, catalog, run_id, provider, backend):
        super().__init__(catalog, run_id, provider, backend)
        self.route = 'dense'

    def _limits(self, limit):
        return {'dense':limit}
