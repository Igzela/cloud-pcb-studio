"""Resolve the pinned Freerouting JVM distribution without network access."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


FREEROUTING_VERSION = "2.5.0"
JAR_NAME = f"freerouting-{FREEROUTING_VERSION}.jar"
JAR_URL = f"https://github.com/freerouting/freerouting/releases/download/v{FREEROUTING_VERSION}/{JAR_NAME}"
JAR_SHA256 = "f6f51bb02245e8e717f9359bd260cc9c5c0b1bc0acc8b7cb2cd5b8ffeb5de3c7"
JRE_DIRECTORY = "jdk-25.0.5+8-jre"
RUNTIME_DIRECTORY = Path(".run/tools/freerouting")


class FreeroutingConfigurationError(RuntimeError):
    """The selected JVM or pinned Freerouting JAR cannot be used."""


@dataclass(frozen=True)
class FreeroutingRuntime:
    command: tuple[str, ...]
    provenance: dict


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def resolve_freerouting_jvm(project_root: Path) -> FreeroutingRuntime:
    """Validate the installed JAR and record the exact Java runtime selected."""
    tool_dir = project_root.resolve() / RUNTIME_DIRECTORY
    jar_path = tool_dir / JAR_NAME
    if not jar_path.is_file() or file_sha256(jar_path) != JAR_SHA256:
        raise FreeroutingConfigurationError(
            f"Missing or altered Freerouting {FREEROUTING_VERSION} JAR at {jar_path}. "
            "Run source scripts/env.sh && python -m scripts.install_freerouting_jvm."
        )

    bundled_java = tool_dir / JRE_DIRECTORY / "bin/java"
    override = os.environ.get("FREEROUTING_JAVA")
    java = shutil.which(override or (str(bundled_java) if bundled_java.is_file() else "java"))
    if not java:
        raise FreeroutingConfigurationError(
            "Java 25 is required. Install the bundled JRE with "
            "python -m scripts.install_freerouting_jvm or set FREEROUTING_JAVA to Java 25."
        )
    java_path = Path(java).resolve()
    try:
        probe = subprocess.run(
            [str(java_path), "-version"], capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FreeroutingConfigurationError(f"Could not run Java at {java_path}: {exc}") from exc
    version_lines = (probe.stderr + "\n" + probe.stdout).strip().splitlines()
    if probe.returncode or not any(re.match(r'(?:openjdk|java) version "25(?:[."-])', line)
                                   for line in version_lines):
        raise FreeroutingConfigurationError(
            f"Java 25 is required at {java_path}; got {version_lines[0] if version_lines else 'no version'}. "
            "Run the installer or set FREEROUTING_JAVA to Java 25."
        )

    java_home = java_path.parent.parent
    java_files = {"bin/java": file_sha256(java_path)}
    for name in ("release", "lib/modules", "lib/server/libjvm.so", "lib/libjava.so",
                 "lib/libjli.so", "conf/security/java.security"):
        path = java_home / name
        if path.is_file():
            java_files[name] = file_sha256(path)
    provenance = {
        "engine": "freerouting_jvm",
        "version": FREEROUTING_VERSION,
        "jar_url": JAR_URL,
        "jar_sha256": JAR_SHA256,
        "java_executable": str(java_path),
        "java_version": version_lines,
        "java_files_sha256": java_files,
    }
    return FreeroutingRuntime((str(java_path), "-jar", str(jar_path)), provenance)
