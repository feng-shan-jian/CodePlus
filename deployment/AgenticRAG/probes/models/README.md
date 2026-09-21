# R03 local CUDA probes

This directory is a bounded Windows capability experiment. It does not implement
the shared worker, scheduler, application provider, translation model, or an API
fallback. Only built-in synthetic text is used. Root dependencies and the existing
repository `.venv` are not changed.

The independent environment is `C:/Users/18221/.cache/codeplus-agenticrag/venv-win-cuda`.
Weights are under `C:/Users/18221/.cache/codeplus-agenticrag/models`. Both are intentionally
retained outside Git for R05/R09 and independent acceptance; do not share them with WSL.

From `D:/CodePlus` in PowerShell 7, initial setup is:

```powershell
uv venv C:/Users/18221/.cache/codeplus-agenticrag/venv-win-cuda --python C:/Python314/python.exe
uv pip install --python C:/Users/18221/.cache/codeplus-agenticrag/venv-win-cuda/Scripts/python.exe torch==2.14.0+cu130 --index-url https://download.pytorch.org/whl/cu130
uv pip install --python C:/Users/18221/.cache/codeplus-agenticrag/venv-win-cuda/Scripts/python.exe -r deployment/AgenticRAG/probes/models/environment-win-cuda.freeze.txt
C:/Users/18221/.cache/codeplus-agenticrag/venv-win-cuda/Scripts/python.exe -B deployment/AgenticRAG/probes/models/assets.py
```

The full resolved dependency inventory is in `environment-win-cuda.freeze.txt`.
Install the CUDA wheel first so the frozen `torch==2.14.0+cu130` is already present;
the official cp314 Windows wheel SHA-256 is
`78ab64d12e478c8baedc4d90e662f6ffca2b6cb8a872a02b734a8aa3e00277eb`.
`assets.py` pins model IDs/revisions, validates official Git blob or LFS identities,
then writes SHA-256 metadata to `models.lock.json`. Existing cache files are only
reused after validation; the older shared HF cache is read-only. Downloading uses
verified HTTPS, finite connection/request/process timeouts, bounded retries and
16 MiB byte ranges for large files. A range response must have exactly the requested
`Content-Range` and length, including the last short range; servers ignoring Range
fail validation. A file is installed only after its complete hash matches. Failed
partial/range/header files are removed. A completed file is rechecked before reuse.

Run all real GPU acceptance assertions once, with an explicit report path:

```powershell
$env:R03_RUN_REAL = '1'
$env:R03_REPORT_PATH = 'D:/CodePlus/deployment/AgenticRAG/docs/implementation-records/R03-observations.json'
$env:PYTHONDONTWRITEBYTECODE = '1'
C:/Users/18221/.cache/codeplus-agenticrag/venv-win-cuda/Scripts/python.exe -B -m pytest -q --tb=short -p no:cacheprovider deployment/AgenticRAG/tests/test_local_model_capabilities.py
```

Use a different report filename for independent reruns. Without `R03_RUN_REAL=1`,
the tests skip and provide **no GPU acceptance evidence**. Once opted in, missing
dependencies, CUDA, pinned files, or resources fail; tests never substitute models.
Model loading is entirely local, checks every pinned file's SHA-256, and disables
remote code. A direct raw run is also available with `probe.py --report <path>`.

The probe reserves at most 2048 MiB in PyTorch's CUDA caching allocator. It requires
at least 2304 MiB from `torch.cuda.mem_get_info()` before starting and leaves other GPU processes untouched.
On Windows WDDM, that free-memory value differs from `nvidia-smi`'s physical-device
accounting. Also check `nvidia-smi --query-gpu=memory.free --format=csv,noheader`
before a rerun and do not start with less than 2304 MiB physically free. R03 recorded
both readings; the probe does not implement production memory admission control.
This is an experiment quota, not the hardware capacity or the total process memory
(CUDA context/driver allocations can be outside the allocator). One model is loaded
at a time, using bfloat16, SDPA, `eval()`, inference mode and `use_cache=False`.

All model inputs are tokenized in full before GPU work. The tentative probe limits
are 2048 tokens per complete input, four items per batch, and 4096 padded tokens per
batch. The pinned configs declare 32768 positions for Embedding and 40960 for Reranker
(the public model cards advertise 32k); those are not tested GPU capacity. Input and batch limits raise explicit errors;
there is no truncation or partial batch success. Exact formatting, fingerprints,
timings and measured scope appear in the capability report.

Timing calls synchronize CUDA before and after each operation. Cold load means a
new model instance: OS disk cache is neither cleared nor claimed cold. A tiny BF16
matmul first establishes a model-free allocator baseline; CUDA BLAS is therefore warm.
Asset hashing/tokenizer setup is measured separately from synchronized weight loading.
Batch timings
include tensor transfer, forward computation and result transfer; tokenizer validation
is outside those timers. Three sequential trials are retained individually; no P95
or production latency guarantee is inferred from three samples. Peak allocated and
reserved memory are process-allocator metrics in MiB, not total GPU usage.

Failure checks launch dedicated short-lived interpreters: `-S` gives a genuinely
missing dependency environment; `CUDA_VISIBLE_DEVICES=-1` makes the device unavailable;
a 32 MiB allocator quota followed by an actual Embedding model load produces a real
model-loading OOM without exhausting the card. Those children must exit 2 with structured diagnostics.
Unloading verifies weak references to the model and all parameters/buffers are dead,
and returns allocator memory to the measured model-free baseline (the observed cuBLAS
workspace stays until process exit). Subprocesses are joined
with timeouts. The run's final report records any failure and cleanup state.

No shared worker, two-client contention, natural physical-card OOM, concurrent model
residency, Linux installation, retrieval benchmark, or production throughput is claimed.
Those scopes remain with their later tasks.
