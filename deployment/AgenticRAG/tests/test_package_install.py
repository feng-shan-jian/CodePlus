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
from agentic_rag.domain import KnowledgeBase, Span, ErrorCode, RagError, KnowledgeRevision, RevisionMember, IndexState, SourceRef
from agentic_rag.capabilities import require_optional_dependencies, require_provider
from agentic_rag.storage import Catalog
from agentic_rag.storage.database import runtime_fingerprint
from agentic_rag.ingestion import InputSelection, select_inputs, capture_inputs, read_input, verify_inputs, process_inputs, read_processed
from agentic_rag.models import FrozenTokenizer
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
    source.unlink()
    processed, = process_inputs(catalog, owner, FrozenTokenizer(config.embedding, pathlib.Path(sys.argv[2])))
    assert processed.stage == 'chunked'
    item,version,chunks=read_processed(catalog,item.batch_id,item.entry.item_id)
    revision=KnowledgeRevision(revision_id=uuid4(),kb_id=library.kb_id,processing_snapshot_id=snapshot.snapshot_id,
        manifest_hash='a'*64,index_state=IndexState.PREPARING)
    member=RevisionMember(revision_id=revision.revision_id,document_id=version.document_id,
        document_version_id=version.document_version_id,chunk_set_hash=next(v.sha256 for v in item.output_hashes if v.kind=='chunks'))
    catalog.add_candidate(owner,revision,(member,))
    # Source-only installation fixture; no claim of service publication.
    with catalog._owned(owner) as connection:
        connection.execute("UPDATE revisions SET index_state='READY' WHERE revision_id=?",(str(revision.revision_id),))
        connection.execute('UPDATE libraries SET current_revision_id=? WHERE kb_id=?',(str(revision.revision_id),str(library.kb_id)))
    owner.abandon()
reopened = Catalog(catalog._directory.root)
raw_item,=reopened.get_input_items(item.batch_id)
assert read_input(reopened, item.batch_id, raw_item.entry.item_id) == b'installed immutable original'
assert verify_inputs(reopened, item.batch_id) == (snapshot, (raw_item,))
assert read_processed(reopened, item.batch_id, raw_item.entry.item_id)[0] == processed
from agentic_rag.sources import SourceSession
from agentic_rag.evidence import DeliveryGateway,MappedSpan
from agentic_rag.citations import CitationRegistry,open_citation
class ControlledMeter:
    identity='installed-controlled-utf8-byte-fixture'
    def count(self,text):return len(text.encode())
with reopened.start_run(library.kb_id,resolve_run(config,'qa')) as lease:
    session=SourceSession(reopened,lease,ControlledMeter())
    ref=SourceRef(kb_id=library.kb_id,revision_id=revision.revision_id,document_id=version.document_id,
        document_version_id=version.document_version_id,section_id=chunks.parsed.sections[0].section_id)
    result=session.open(session.issue_source(ref))
    gateway=DeliveryGateway(session);gateway.bind_tool_result(result,'installed-call')
    body=json.dumps({'messages':[{'role':'tool','tool_call_id':'installed-call','content':result.text}]}).encode()
    mappings=tuple(MappedSpan(m.candidate_id,m.source_span,('messages',0,'content'),m.body_span,('messages',0,'tool_call_id')) for m in result.body_mappings)
    permit=gateway.prepare(body,mappings,purpose='explore',protocol='compat')
    evidence_id,=gateway.settle(permit,'confirmed')
    saved=CitationRegistry(session).save(evidence_id,(Span(start=0,end=9),),('installed',))
from uuid import UUID
assert open_citation(reopened,UUID(saved['citation']['citation_id']))==saved
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
for name in ('codeplus', 'torch', 'transformers', 'pymilvus', 'textual'):
    assert importlib.util.find_spec(name) is None, name
    assert not any(key == name or key.startswith(name + '.') for key in sys.modules), name
try:
    import agentic_rag.adapters.codeplus
except ImportError as error:
    assert 'locally built CodePlus host' in str(error)
else:
    raise AssertionError('host adapter must diagnose the absent optional host')
assert 'codeplus' not in sys.modules and 'torch' not in sys.modules
requirements = metadata.requires('codeplus-agentic-rag')
core_requirements = [item for item in requirements if 'extra ==' not in item]
assert len(core_requirements) == 4 and 'apsw==3.53.4.0' in requirements and any(item.startswith('pydantic') for item in requirements)
assert any('torch==2.14.0+cu130' in item and 'local-models' in item for item in requirements)
assert 'markdown-it-py==4.0.0' in requirements and 'tokenizers==0.23.2' in requirements
distribution = metadata.distribution('codeplus-agentic-rag')
assert not distribution.entry_points
installed = {dist.metadata['Name']: dist.version for dist in metadata.distributions()}
print(json.dumps({'executable':sys.executable, 'python':sys.version, 'prefix':str(root),
                  'cwd':str(pathlib.Path.cwd()), 'import_path':str(location), 'isolated':bool(sys.flags.isolated),
                  'version':agentic_rag.__version__, 'requirements':requirements, 'installed':installed,
                  'sqlite':sqlite, 'storage_roundtrip':True, 'input_snapshot_roundtrip':True,
                  'manifest_hash':manifest.identity, 'raw_hash':raw_item.raw.sha256, 'parsed_checkpoint_roundtrip':True,
                  'source_delivery_citation_roundtrip':True,'delivery_is_controlled_core_fixture_not_http':True,
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

    def command(args, cwd=outside, expected=0):
        completed = subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, text=True,
                                   encoding="utf-8", errors="replace", capture_output=True, timeout=180)
        report["commands"].append({"argv": [str(arg) for arg in args], "cwd": str(cwd),
                                   "exit_code": completed.returncode, "stdout": completed.stdout,
                                   "stderr": completed.stderr})
        assert completed.returncode == expected, completed.stdout + completed.stderr
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
                            (package / "src/agentic_rag").rglob("*") if file.suffix in {".py", ".sql", ".json"}}
        root_sources = {"agentic_rag/" + name for name in
                            ("__init__.py", "_schema.py", "capabilities.py", "config.py", "domain.py", "profiles.py", "source_archive.py", "sources.py", "evidence.py", "citations.py", "model_switch.py")}
        storage_sources = {"agentic_rag/storage/" + name for name in
                           ("__init__.py", "archives.py", "catalog.py", "database.py", "locks.py",
                            "ownership.py", "paths.py", "runs.py", "schema.sql", "inputs.py", "inputs.sql", "processing.py", "processing.sql", "publication.py", "publication.sql", "evidence.sql", "host_runs.sql", "mutations.sql", "recovery.py", "recovery.sql", "readers.py", "gc.py", "lifetimes.sql", "model_switches.sql", "retrieval.sql")}
        ingestion_sources = {"agentic_rag/ingestion/" + name for name in
                             ("__init__.py", "records.py", "source.py", "selection.py", "capture.py", "parsing.py", "chunking.py", "processing.py", "build.py", "mutations.py", "encoding.py", "recovery.py")}
        model_sources = {'agentic_rag/models/' + name for name in ('__init__.py', 'tokenization.py',
            'protocol.py', 'identity.py', '_windows.py', 'engine.py', 'worker.py', 'lifecycle.py', 'client.py', 'models.lock.json')}
        index_sources = {'agentic_rag/indexes/'+name for name in ('__init__.py','manifest.py','milvus.py')}
        retrieval_sources = {'agentic_rag/retrieval/'+name for name in ('__init__.py','dense.py','search.py','rrf.py','context.py')}
        adapter_sources = {'agentic_rag/adapters/codeplus/'+name for name in
                           ('__init__.py','meter.py','ledger.py','policy.py','_vendor/__init__.py','_vendor/deepseek_v41.py')}
        assert expected_sources == root_sources | storage_sources | ingestion_sources | model_sources | index_sources | retrieval_sources | adapter_sources
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
                    expected = {"src/" + name for name in expected_sources} | {"pyproject.toml", "README.md", "LICENSE", "uv.lock", "PKG-INFO", ".gitignore", "requirements-local-models-win-py314.lock"}
                    assert relative == expected
            report["artifacts"].append({"path": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                                        "size_bytes": artifact.stat().st_size, "members": names})
        requirements = tmp_path / "runtime-requirements.txt"
        command([uv, "export", "--project", package, "--locked", "--no-dev", "--no-emit-project",
                 "--no-header", "--output-file", requirements])
        helper = runpy.run_path(str(package / "tests/parsing_support.py"))
        config = helper['configuration']().model_dump(mode='json')
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
            evidence = json.loads(command([python, "-I", "-B", smoke, config_path, helper['cache']()]))
            evidence["route"] = label
            assert not Path(evidence["cwd"]).is_relative_to(package)
            assert Path(evidence["import_path"]).is_relative_to(install)
            report["installations"].append(evidence)
            missing_report=outside/(label+'-missing-milvus.json')
            command([python,'-I','-B',package/'eval/dense_runner.py','dense','--root',outside/(label+'-missing-data'),
                     '--endpoint','http://127.0.0.1:19532','--cuda-python',python,'--model-cache',helper['cache'](),
                     '--report',missing_report,'--ids',package/'eval/development-ids.json',
                     '--dataset-hash','f80fc4033be6625b19da2af9529cf925d147e9ad62c95b943df2c3d08ec2e898'],expected=1)
            missing=json.loads(missing_report.read_text(encoding='utf-8'))
            assert missing['dependencies']['pymilvus'] is None
            assert len(missing['records'])==missing['errors']==200
            assert all(r['status']=='error' and r['hits']==[] and r['error'] for r in missing['records'])
            assert missing['error']['type']=='ModuleNotFoundError'
            evidence['missing_milvus_extra']={'result':missing['result'],'records':200,'errors':200,
                'exception':missing['error'],'report_sha256':hashlib.sha256(missing_report.read_bytes()).hexdigest()}
        report["result"] = "PASS"
    finally:
        evidence_path = os.environ.get("R11_INSTALL_REPORT") or os.environ.get("R10_INSTALL_REPORT") or os.environ.get("R09_INSTALL_REPORT") or os.environ.get("R08_INSTALL_REPORT") or os.environ.get("R07_INSTALL_REPORT") or os.environ.get("R06_INSTALL_REPORT") or os.environ.get("R05_INSTALL_REPORT")
        if evidence_path:
            Path(evidence_path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
