"""Real artifacts and isolated installations. Requires uv, not a GPU or host."""

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import runpy
import shutil
import subprocess
import sys
import tarfile
import zipfile


SMOKE = r'''
import importlib.metadata as metadata
import importlib.util
import io, json, pathlib, sys
from uuid import uuid4
import agentic_rag
from agentic_rag.config import KnowledgeConfig, ProcessingSnapshot, RunOverride, resolve_run
from agentic_rag.domain import KnowledgeBase, Span, ErrorCode, RagError
from agentic_rag.capabilities import require_optional_dependencies, require_provider
from agentic_rag.storage import Catalog
from agentic_rag.storage.database import runtime_fingerprint
from agentic_rag.ingestion import InputSelection, select_inputs, capture_inputs, read_input, verify_inputs
root = pathlib.Path(sys.prefix).resolve()
location = pathlib.Path(agentic_rag.__file__).resolve()
assert location.is_relative_to(root) and 'site-packages' in location.parts, location
assert sys.flags.isolated and sys.flags.ignore_environment
assert metadata.version('codeplus-agentic-rag') == agentic_rag.__version__ == '0.1.0'
config = KnowledgeConfig.model_validate_json(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
snapshot = ProcessingSnapshot.capture(uuid4(), config)
assert ProcessingSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
assert resolve_run(config, 'report', RunOverride(mode='fixed')).retrieval.mode == 'fixed'
assert config.retrieval.mode == 'auto'
assert KnowledgeBase(kb_id=uuid4(), name='installed').name == 'installed'
assert Span.model_validate_json('{"start":0,"end":4}').end == 4
catalog = Catalog(pathlib.Path(sys.argv[1]).parent / ('installed-data-' + str(uuid4())))
library = catalog.create_library('installed storage')
archive = catalog.archives.put(io.BytesIO(b'installed archive'))
assert catalog.archives.read(archive.sha256) == b'installed archive'
with catalog.begin_mutation(library.kb_id, snapshot, 'a' * 64) as owner:
    assert catalog.get_batch(owner.token.batch_id).owner_epoch == 1
    owner.abandon()
assert Catalog(catalog._directory.root).get_library(library.kb_id).name == 'installed storage'
source = pathlib.Path(sys.argv[1]).parent / 'installed source 中文.txt'
source.write_bytes(b'installed immutable original')
manifest = select_inputs((InputSelection(path=str(source)),))
with catalog.begin_import(library.kb_id, snapshot, manifest) as owner:
    item, = capture_inputs(catalog, owner)
    assert item.stage == 'captured' and item.change == 'new'
    owner.abandon()
source.unlink()
reopened = Catalog(catalog._directory.root)
assert read_input(reopened, item.batch_id, item.entry.item_id) == b'installed immutable original'
assert verify_inputs(reopened, item.batch_id) == (snapshot, (item,))
sqlite = runtime_fingerprint()
assert sqlite['apsw'] == '3.53.4.0' and sqlite['sqlite'] == '3.53.4'
for action, code in ((lambda: require_optional_dependencies('embedding'), ErrorCode.DEPENDENCY_UNAVAILABLE),
                     (lambda: require_provider(None, 'rerank'), ErrorCode.CAPABILITY_UNAVAILABLE)):
    try:
        action()
    except RagError as exc:
        assert exc.error.code == code and exc.error.stage
    else:
        raise AssertionError('missing capability must fail explicitly')
for name in ('codeplus', 'torch', 'transformers', 'tokenizers', 'pymilvus', 'textual'):
    assert importlib.util.find_spec(name) is None, name
    assert not any(key == name or key.startswith(name + '.') for key in sys.modules), name
requirements = metadata.requires('codeplus-agentic-rag')
assert len(requirements) == 2 and 'apsw==3.53.4.0' in requirements and any(item.startswith('pydantic') for item in requirements)
distribution = metadata.distribution('codeplus-agentic-rag')
assert not distribution.entry_points
installed = {dist.metadata['Name']: dist.version for dist in metadata.distributions()}
print(json.dumps({'executable':sys.executable, 'python':sys.version, 'prefix':str(root),
                  'cwd':str(pathlib.Path.cwd()), 'import_path':str(location), 'isolated':bool(sys.flags.isolated),
                  'version':agentic_rag.__version__, 'requirements':requirements, 'installed':installed,
                  'sqlite':sqlite, 'storage_roundtrip':True, 'input_snapshot_roundtrip':True,
                  'manifest_hash':manifest.identity, 'raw_hash':item.raw.sha256,
                  'document_encoding_fingerprint':snapshot.document_encoding_fingerprint,
                  'forbidden_modules_loaded':[], 'missing_dependencies_diagnostic':True}))
'''


def test_wheel_and_sdist_install_in_isolated_environments(tmp_path):
    package = Path(__file__).resolve().parents[1]
    uv = shutil.which("uv")
    assert uv, "uv is required for real package installation acceptance"
    outside = tmp_path / "outside repository 中文"
    outside.mkdir()
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    report = {"platform": platform.platform(), "build_python": sys.executable,
              "build_dependencies": {name: importlib.metadata.version(name) for name in
                                     ("hatchling", "packaging", "pathspec", "pluggy", "trove-classifiers")},
              "commands": [], "artifacts": [], "installations": []}

    def command(args, cwd=outside):
        completed = subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, text=True,
                                   encoding="utf-8", errors="replace", capture_output=True, timeout=180)
        report["commands"].append({"argv": [str(arg) for arg in args], "cwd": str(cwd),
                                   "exit_code": completed.returncode, "stdout": completed.stdout,
                                   "stderr": completed.stderr})
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return completed.stdout

    try:
        build_flags = ["--python", sys.executable, "--no-build-isolation", "--no-create-gitignore"]
        direct, source, rebuilt = tmp_path / "direct", tmp_path / "source", tmp_path / "rebuilt"
        command([uv, "build", package, "--wheel", "--out-dir", direct, *build_flags])
        command([uv, "build", package, "--sdist", "--out-dir", source, *build_flags])
        sdist, = source.glob("*.tar.gz")
        command([uv, "build", sdist, "--wheel", "--out-dir", rebuilt, *build_flags])
        direct_wheel, = direct.glob("*.whl")
        rebuilt_wheel, = rebuilt.glob("*.whl")
        expected_sources = {file.relative_to(package / "src").as_posix() for file in
                            (package / "src/agentic_rag").rglob("*") if file.suffix in {".py", ".sql"}}
        root_sources = {"agentic_rag/" + name for name in
                        ("__init__.py", "_schema.py", "capabilities.py", "config.py", "domain.py", "profiles.py")}
        storage_sources = {"agentic_rag/storage/" + name for name in
                           ("__init__.py", "archives.py", "catalog.py", "database.py", "locks.py",
                            "ownership.py", "paths.py", "runs.py", "schema.sql", "inputs.py", "inputs.sql")}
        ingestion_sources = {"agentic_rag/ingestion/" + name for name in
                             ("__init__.py", "records.py", "source.py", "selection.py", "capture.py")}
        assert expected_sources == root_sources | storage_sources | ingestion_sources
        for artifact in (direct_wheel, sdist, rebuilt_wheel):
            if artifact.suffix == ".whl":
                with zipfile.ZipFile(artifact) as archive:
                    names = sorted(archive.namelist())
                    code = {name for name in names if name.startswith("agentic_rag/")}
                    assert code == expected_sources
                    assert any(name.endswith("/licenses/LICENSE") for name in names)
                    assert all(name.startswith(("agentic_rag/", "codeplus_agentic_rag-0.1.0.dist-info/")) for name in names)
            else:
                with tarfile.open(artifact) as archive:
                    names = sorted(member.name for member in archive.getmembers() if member.isfile())
                    relative = {name.split("/", 1)[1] for name in names}
                    # Hatch preserves the package's own VCS exclusion manifest in
                    # sdist for reproducible rebuilding (force-include metadata).
                    expected = {"src/" + name for name in expected_sources} | {"pyproject.toml", "README.md", "LICENSE", "uv.lock", "PKG-INFO", ".gitignore"}
                    assert relative == expected
            report["artifacts"].append({"path": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                                        "size_bytes": artifact.stat().st_size, "members": names})
        requirements = tmp_path / "runtime-requirements.txt"
        command([uv, "export", "--project", package, "--locked", "--no-dev", "--no-emit-project",
                 "--no-header", "--output-file", requirements])
        config = runpy.run_path(str(package / "tests/test_configuration.py"))["example_config"]()
        config_path = outside / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        smoke = outside / "installed_smoke.py"
        smoke.write_text(SMOKE, encoding="utf-8")
        for label, wheel in (("direct-wheel", direct_wheel), ("sdist-wheel", rebuilt_wheel)):
            install = tmp_path / label
            assert not install.exists()
            command([uv, "venv", install, "--python", sys.executable])
            python = install / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            command([uv, "pip", "install", "--python", python, "--require-hashes", "-r", requirements])
            command([uv, "pip", "install", "--python", python, "--no-deps", wheel])
            command([uv, "pip", "check", "--python", python])
            evidence = json.loads(command([python, "-I", "-B", smoke, config_path]))
            evidence["route"] = label
            assert not Path(evidence["cwd"]).is_relative_to(package)
            assert Path(evidence["import_path"]).is_relative_to(install)
            report["installations"].append(evidence)
        report["result"] = "PASS"
    finally:
        evidence_path = os.environ.get("R07_INSTALL_REPORT") or os.environ.get("R06_INSTALL_REPORT") or os.environ.get("R05_INSTALL_REPORT")
        if evidence_path:
            Path(evidence_path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
