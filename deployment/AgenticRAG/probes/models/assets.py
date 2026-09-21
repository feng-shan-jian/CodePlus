"""Fetch only fixed, public Qwen files into the dedicated R03 cache.

HTTPS certificate verification is never disabled. Existing embedding artifacts
may be copied read-only, but are verified against the official revision first.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


MODELS = {
    "embedding": ("Qwen/Qwen3-Embedding-0.6B", "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"),
    "reranker": ("Qwen/Qwen3-Reranker-0.6B", "e61197ed45024b0ed8a2d74b80b4d909f1255473"),
}
FILES = {"config.json", "tokenizer.json", "tokenizer_config.json", "vocab.json",
         "merges.txt", "model.safetensors", "generation_config.json", "chat_template.jinja"}
DEFAULT_CACHE = Path.home() / ".cache/codeplus-agenticrag/models"


def digest(path: Path, algorithm="sha256", git_blob=False):
    hasher = hashlib.new(algorithm)
    if git_blob:
        hasher.update(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def upstream_matches(path, entry):
    if not path.is_file() or path.stat().st_size != entry["size"]:
        return False
    lfs = entry.get("lfs")
    return (digest(path) == lfs["sha256"] if lfs else
            digest(path, "sha1", git_blob=True) == entry["blobId"])


def download(url, path, size):
    # Bounded ranges avoid long, idle TLS streams on this Windows network.
    if size <= 32 * 1024 * 1024:
        subprocess.run(["curl.exe", "-fsSL", "--connect-timeout", "30", "--max-time", "180",
            "--retry", "3", "--retry-all-errors", "--output", str(path), url], check=True, timeout=750)
        return
    chunk = path.with_suffix(".chunk")
    headers = path.with_suffix(".headers")
    try:
        with path.open("wb") as output:
            for start in range(0, size, 16 * 1024 * 1024):
                end = min(start + 16 * 1024 * 1024, size) - 1
                subprocess.run(["curl.exe", "-fsSL", "--connect-timeout", "30", "--max-time", "180",
                    "--retry", "3", "--retry-all-errors", "--range", f"{start}-{end}", "--dump-header", str(headers),
                    "--output", str(chunk), url], check=True, timeout=750)
                ranges = re.findall(r"content-range:\s*bytes (\d+)-(\d+)/(\d+)", headers.read_text(), re.I)
                if not ranges or tuple(map(int, ranges[-1])) != (start, end, size) or chunk.stat().st_size != end-start+1:
                    raise RuntimeError("DOWNLOAD_RANGE_MISMATCH: server did not return requested byte range")
                with chunk.open("rb") as stream:
                    shutil.copyfileobj(stream, output)
                print(f"downloaded {end+1}/{size} bytes", flush=True)
    finally:
        chunk.unlink(missing_ok=True)
        headers.unlink(missing_ok=True)


def fetch(cache: Path, manifest: Path):
    result = {"schema": 1, "models": {}}
    for capability, (model_id, revision) in MODELS.items():
        api = f"https://huggingface.co/api/models/{model_id}/revision/{revision}?blobs=true"
        info = json.loads(subprocess.check_output(["curl.exe", "-fsSL", "--connect-timeout", "30",
            "--max-time", "120", "--retry", "3", "--retry-all-errors", api], timeout=520))
        if info["sha"] != revision or info["id"] != model_id:
            raise RuntimeError("MODEL_IDENTITY_MISMATCH: official revision does not match")
        target = cache / model_id.split("/")[-1] / revision
        target.mkdir(parents=True, exist_ok=True)
        files = {}
        for entry in info["siblings"]:
            name = entry["rfilename"]
            if name not in FILES:
                continue
            path = target / name
            if not upstream_matches(path, entry):
                old = (Path.home() / ".cache/huggingface/hub" /
                       ("models--" + model_id.replace("/", "--")) / "snapshots" / revision / name)
                part = path.with_name(name + ".part")
                try:
                    if upstream_matches(old, entry):
                        shutil.copyfile(old, part)
                    else:
                        url = f"https://huggingface.co/{model_id}/resolve/{revision}/{name}"
                        download(url, part, entry["size"])
                    if not upstream_matches(part, entry):
                        raise RuntimeError(f"MODEL_IDENTITY_MISMATCH: {model_id}/{name}")
                    part.replace(path)
                finally:
                    part.unlink(missing_ok=True)
            files[name] = {"size": path.stat().st_size, "sha256": digest(path),
                           "upstream_git_blob": entry["blobId"],
                           "upstream_lfs_sha256": entry.get("lfs", {}).get("sha256")}
            print(f"verified {capability}/{name}", flush=True)
        result["models"][capability] = {"model_id": model_id, "revision": revision,
            "license": info["cardData"]["license"], "source": api,
            "relative_cache_path": f"{model_id.split('/')[-1]}/{revision}", "files": files}
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("models.lock.json"))
    args = parser.parse_args()
    fetch(args.cache, args.manifest)
