# Attribution and provenance

MultiHop-RAG: Benchmarking Retrieval-Augmented Generation for Multi-Hop Queries.
Authors: Yixuan Tang and Yi Yang.

- Official repository: https://github.com/yixuantt/MultiHop-RAG
- Dataset: https://huggingface.co/datasets/yixuantt/MultiHopRAG
- Paper: https://arxiv.org/abs/2401.15391
- Upstream declares the Open Data Commons Attribution License (ODC-BY): https://opendatacommons.org/licenses/by/1-0/

The dataset JSON and scoring scripts in `upstream/` are pinned, unmodified copies. Exact revisions, download URLs and SHA256 hashes are recorded in `source-lock.json`. Original article titles, publishers, authors, dates and URLs remain in `upstream/corpus.json` and the adapter manifest. Article body text is preserved in the Markdown exports. The dataset license does not transfer ownership of the underlying publishers' articles.

CodePlus adds stable IDs, source offsets, importable files, fixed local sampling tiers and execution wrappers. It does not claim authorship of the upstream questions, answers, evidence, documents or scoring functions. The local lite and medium samples are not official MultiHop-RAG releases; no paper-result reproduction is claimed.
