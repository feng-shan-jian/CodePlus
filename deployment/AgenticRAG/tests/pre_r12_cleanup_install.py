"""Build and inspect the cleaned host in two fresh, isolated installations.

Run with --repo, --work (an absent owned directory), and --report. This exercises
the real build backend, resolver, installed modules and CLI; it makes no network
model or GPU/Milvus claim. Both installs use the current locked dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
import zipfile


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def smoke():
    import codeplus
    import codeplus.agent
    import codeplus.app
    import codeplus.config
    import codeplus.remote
    import codeplus.__main__
    from codeplus.commands import CommandRegistry
    from codeplus.commands.handlers import register_all_commands
    from codeplus.config import AppConfig

    assert "site-packages" in Path(codeplus.__file__).parts
    assert importlib.util.find_spec("codeplus.knowledge") is None
    assert importlib.util.find_spec("codeplus.tools.knowledge") is None
    assert importlib.util.find_spec("codeplus.source_preview") is None
    assert importlib.util.find_spec("agentic_rag") is None
    registry = CommandRegistry()
    register_all_commands(registry)
    assert registry.find("knowledge") is None
    assert not hasattr(AppConfig(providers=[]), "knowledge")
    metadata = importlib.metadata.metadata("codeplus")
    assert "knowledge" not in (metadata.get_all("Provides-Extra") or [])
    modules = sorted(name for name in sys.modules if name.split(".")[0] in
                     {"torch", "transformers", "pymilvus", "llama_index", "agentic_rag"})
    assert not modules
    print(json.dumps({"python": sys.version, "module": codeplus.__file__,
                      "distribution": importlib.metadata.version("codeplus"),
                      "retired_modules_absent": True, "optional_modules_loaded": modules,
                      "root_package_only": True}))


def verify(repo, work, report):
    assert repo.is_absolute() and work.is_absolute() and not work.exists()
    assert not work.is_relative_to(repo)
    work.mkdir(parents=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", "UV_LINK_MODE": "copy"}
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    commands = []
    result = {"repo": str(repo), "work": str(work), "commands": commands,
              "model_evaluation": "not_executed", "gpu_milvus": "not_executed", "installations": []}

    def save():
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    def run(args, cwd=work):
        started = time.monotonic()
        proc = subprocess.run(list(map(str, args)), cwd=cwd, env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=300)
        row = {"argv": list(map(str, args)), "cwd": str(cwd), "exit_code": proc.returncode,
               "seconds": time.monotonic() - started, "stdout": proc.stdout, "stderr": proc.stderr}
        commands.append(row)
        save()
        assert proc.returncode == 0, row
        return proc.stdout

    run(["uv", "build", "--offline", "--wheel", "--out-dir", work / "direct", repo])
    run(["uv", "build", "--offline", "--sdist", "--out-dir", work / "sdist", repo])
    sdist = next((work / "sdist").glob("*.tar.gz"))
    with tarfile.open(sdist) as archive:
        names = [entry.name.split("/", 1)[-1] for entry in archive.getmembers() if entry.isfile()]
        assert not any(name.startswith(("deployment/", "eval/", ".venv/", ".git/", "codeplus/knowledge/")) for name in names)
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
    result["sdist"] = {"sha256": digest(sdist), "file_count": len(names), "files": names}
    run(["uv", "build", "--offline", "--wheel", "--out-dir", work / "rebuilt", sdist])
    requirements = work / "runtime-requirements.txt"
    run(["uv", "export", "--locked", "--offline", "--no-dev", "--no-emit-project",
         "--format", "requirements-txt", "--output-file", requirements], cwd=repo)
    all_packages = []
    for route in ("direct", "rebuilt"):
        wheel = next((work / route).glob("*.whl"))
        with zipfile.ZipFile(wheel) as archive:
            files = archive.namelist()
            assert not any(name.startswith(("deployment/", "eval/", "agentic_rag/", "codeplus/knowledge/")) for name in files)
            assert not any("__pycache__" in name or name.endswith(".pyc") for name in files)
            package = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in files if name.startswith("codeplus/")}
        all_packages.append(package)
        environment = work / (route + "-env")
        run(["uv", "venv", "--python", sys.executable, environment])
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        # Cache availability varies by interpreter/platform. Network downloads
        # are allowed only for the exact versions and hashes from uv.lock.
        run(["uv", "pip", "install", "--python", python, "--require-hashes", "-r", requirements])
        run(["uv", "pip", "install", "--offline", "--no-deps", "--python", python, wheel])
        run(["uv", "pip", "check", "--python", python])
        installed = json.loads(run([python, "-I", "-B", Path(__file__).resolve(), "--smoke"]))
        location = Path(installed["module"]).parent.parent
        assert all(digest(location / name) == value for name, value in package.items())
        help_text = run([python, "-I", "-B", "-m", "codeplus", "--help"])
        assert "--knowledge" not in help_text and "--output-format" in help_text
        result["installations"].append({"route": route, "wheel_sha256": digest(wheel), "file_count": len(files),
                                        "package_files": package, "smoke": installed, "cli_help": True,
                                        "installed_file_hashes_match": True})
        save()
    assert all_packages[0] == all_packages[1]
    result["source_package_parity"] = True
    result["status"] = "passed"
    save()
    print(json.dumps({"status": "passed", "report": str(report), "installations": len(result["installations"])}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.smoke:
        smoke()
    else:
        assert args.repo and args.work and args.report
        verify(args.repo.resolve(), args.work.resolve(), args.report.resolve())
