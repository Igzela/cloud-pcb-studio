"""Install official, checksum-pinned Freerouting JVM files into .run/."""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import tarfile
import tempfile
from pathlib import Path
from urllib.request import urlopen

from cloud_pcb.layout_route.freerouting_runtime import (
    JAR_NAME,
    JAR_SHA256,
    JAR_URL,
    JRE_DIRECTORY,
    RUNTIME_DIRECTORY,
    FreeroutingConfigurationError,
    file_sha256,
    resolve_freerouting_jvm,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JRE_NAME = "OpenJDK25U-jre_x64_linux_hotspot_25.0.5_8.tar.gz"
JRE_URL = f"https://github.com/adoptium/temurin25-binaries/releases/download/jdk-25.0.5%2B8/{JRE_NAME}"
JRE_SHA256 = "6fac98f0c8202efc3135a9e9e42aa61963ec42d1c5cbb40caf231e7b14a24ef6"


def download_verified(url: str, destination: Path, expected_sha256: str) -> None:
    """Publish only a complete file that matches the pinned release digest."""
    with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
        downloaded = Path(directory) / destination.name
        with urlopen(url, timeout=60) as release, downloaded.open("wb") as stream:
            shutil.copyfileobj(release, stream)
        if file_sha256(downloaded) != expected_sha256:
            raise FreeroutingConfigurationError(f"Release checksum mismatch at {url}")
        downloaded.replace(destination)


def install(project_root: Path, *, install_java: bool) -> dict:
    tool_dir = project_root / RUNTIME_DIRECTORY
    tool_dir.mkdir(parents=True, exist_ok=True)
    jar = tool_dir / JAR_NAME
    if not jar.is_file() or file_sha256(jar) != JAR_SHA256:
        download_verified(JAR_URL, jar, JAR_SHA256)

    jre = tool_dir / JRE_DIRECTORY
    if install_java and not jre.exists():
        if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
            raise FreeroutingConfigurationError(
                "The bundled JRE installer supports Linux x86_64. "
                "Install Java 25 for this machine and rerun with --jar-only."
            )
        archive = tool_dir / JRE_NAME
        if not archive.is_file() or file_sha256(archive) != JRE_SHA256:
            download_verified(JRE_URL, archive, JRE_SHA256)
        with tempfile.TemporaryDirectory(dir=tool_dir) as directory:
            staging = Path(directory)
            with tarfile.open(archive, "r:gz") as release:
                release.extractall(staging, filter="data")
            (staging / JRE_DIRECTORY).rename(jre)

    return resolve_freerouting_jvm(project_root).provenance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jar-only", action="store_true", help="Use existing Java 25 instead of installing the Linux x86_64 JRE.")
    args = parser.parse_args()
    try:
        provenance = install(PROJECT_ROOT, install_java=not args.jar_only)
    except (OSError, tarfile.TarError, FreeroutingConfigurationError) as exc:
        parser.exit(1, f"Freerouting setup failed: {exc}\n")
    print(json.dumps(provenance, indent=2))


if __name__ == "__main__":
    main()
