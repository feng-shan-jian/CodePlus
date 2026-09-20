"""Start only the explicitly selected, fixed CodePlus Compose deployment."""

from importlib.resources import files
import json
import os
from pathlib import Path
import subprocess
from tempfile import gettempdir
from urllib.parse import urlsplit


PROJECT = "codeplus-knowledge"
DISTRO = "Ubuntu-24.04"
MANAGED_URI = "http://127.0.0.1:19530"


def compose_path():
    packaged = Path(str(files("codeplus.knowledge").joinpath("compose.yaml")))
    if packaged.is_file():
        return packaged
    # Source checkout; the wheel contains this same file through force-include.
    return Path(__file__).resolve().parents[2] / "deployment/knowledge/compose.yaml"


class ManagedRuntime:
    def __init__(self):
        self._keeper = None

    def _run(self, args, *, timeout=30):
        command = (["wsl.exe", "-d", DISTRO, "--exec"] if os.name == "nt" else []) + args
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=timeout,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"无法运行受管服务命令；请安装/检查 {DISTRO} 内 Docker Engine 和 Compose。{exc}") from exc
        if result.returncode:
            raise RuntimeError("受管服务命令失败；请检查 Docker Engine/Compose：" + result.stderr.strip()[-2000:])
        return result.stdout.strip()

    def hold(self):
        if os.name == "nt" and (self._keeper is None or self._keeper.poll() is not None):
            self.close()
            # EOF on our own pipe ends cat, including when the parent process dies.
            self._keeper = subprocess.Popen(
                ["wsl.exe", "-d", DISTRO, "--exec", "cat"], stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

    def start(self, progress, connect):
        from filelock import FileLock
        import yaml

        self.hold()
        # All worktrees and installed copies for this OS user address the same project.
        with FileLock(Path(gettempdir()) / "codeplus-knowledge-compose.lock", timeout=300):
            try:
                return connect()
            except Exception:
                pass
            path = compose_path()
            specification = yaml.safe_load(path.read_text(encoding="utf-8"))
            ids = self._run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJECT}"]).split()
            if ids:
                for container in json.loads(self._run(["docker", "inspect", *ids])):
                    labels = container["Config"].get("Labels") or {}
                    service = labels.get("com.docker.compose.service")
                    expected = specification["services"].get(service)
                    if (not expected or container["Config"]["Image"] != expected["image"]
                            or labels.get("com.docker.compose.project") != PROJECT):
                        raise RuntimeError("受管 Compose 项目身份不匹配；请人工核对 codeplus-knowledge，未修改服务。")
                    if service == "standalone":
                        ports = container["HostConfig"].get("PortBindings") or {}
                        if ports.get("19530/tcp") != [{"HostIp": "127.0.0.1", "HostPort": str(urlsplit(MANAGED_URI).port)}]:
                            raise RuntimeError("受管 Milvus 端口配置不匹配；未修改服务。")
            progress("正在启动服务")
            location = self._run(["wslpath", "-a", path.as_posix()]) if os.name == "nt" else str(path)
            self._run(["docker", "compose", "-p", PROJECT, "-f", location,
                       "up", "-d", "--no-recreate", "--wait", "--wait-timeout", "240"], timeout=300)
            return connect()

    def close(self):
        if self._keeper is not None:
            self._keeper.stdin.close()
            try:
                self._keeper.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._keeper.terminate()
                self._keeper.wait(timeout=5)
            self._keeper = None
