"""Checksum and runtime selection tests without fetching or launching Java."""

import hashlib
import io
import os
import subprocess

import pytest

from cloud_pcb.layout_route import freerouting_runtime as runtime
from scripts import install_freerouting_jvm as installer


@pytest.fixture
def runtime_files(tmp_path, monkeypatch):
    tool_dir = tmp_path / runtime.RUNTIME_DIRECTORY
    java = tool_dir / runtime.JRE_DIRECTORY / "bin/java"
    java.parent.mkdir(parents=True)
    java.write_bytes(b"java executable")
    java.chmod(0o700)
    jar = tool_dir / runtime.JAR_NAME
    jar.write_bytes(b"verified release")
    monkeypatch.setattr(runtime, "JAR_SHA256", hashlib.sha256(jar.read_bytes()).hexdigest())
    monkeypatch.delenv("FREEROUTING_JAVA", raising=False)
    return java, jar


def test_resolver_identifies_verified_launch_and_java_runtime(tmp_path, monkeypatch, runtime_files):
    java, jar = runtime_files
    java_libs = java.parent.parent / "lib"
    java_libs.mkdir()
    modules = java_libs / "modules"
    modules.write_bytes(b"runtime data")

    def probe(command, **kwargs):
        assert command == [str(java), "-version"]
        assert kwargs["timeout"] == 10
        return subprocess.CompletedProcess(command, 0, "", 'openjdk version "25.0.5" 2026-10-20\n')

    monkeypatch.setattr(runtime.subprocess, "run", probe)
    result = runtime.resolve_freerouting_jvm(tmp_path)
    assert result.command == (str(java), "-jar", str(jar))
    assert result.provenance["jar_sha256"] == hashlib.sha256(jar.read_bytes()).hexdigest()
    assert result.provenance["java_files_sha256"]["lib/modules"] == hashlib.sha256(modules.read_bytes()).hexdigest()
    modules.write_bytes(b"changed runtime")
    assert runtime.resolve_freerouting_jvm(tmp_path).provenance != result.provenance


def test_unverified_jar_fails_before_starting_java(tmp_path, monkeypatch, runtime_files):
    _, jar = runtime_files
    jar.write_bytes(b"unexpected binary")

    def forbidden(*_args, **_kwargs):
        pytest.fail("Java must not start with a mismatched JAR")

    monkeypatch.setattr(runtime.subprocess, "run", forbidden)
    with pytest.raises(runtime.FreeroutingConfigurationError, match="Missing or altered"):
        runtime.resolve_freerouting_jvm(tmp_path)


def test_explicit_java_is_not_silently_replaced_on_version_error(tmp_path, monkeypatch, runtime_files):
    system_java = tmp_path / "java 21"
    system_java.write_bytes(b"old java")
    system_java.chmod(0o700)
    monkeypatch.setenv("FREEROUTING_JAVA", os.fspath(system_java))

    def probe(command, **_kwargs):
        assert command == [str(system_java), "-version"]
        return subprocess.CompletedProcess(command, 0, "", 'openjdk version "21.0.13"')

    monkeypatch.setattr(runtime.subprocess, "run", probe)
    with pytest.raises(runtime.FreeroutingConfigurationError, match="Java 25 is required"):
        runtime.resolve_freerouting_jvm(tmp_path)


def test_download_bad_checksum_keeps_existing_release(tmp_path, monkeypatch):
    jar = tmp_path / "release.jar"
    jar.write_bytes(b"existing release")
    monkeypatch.setattr(installer, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"bad release"))
    with pytest.raises(runtime.FreeroutingConfigurationError, match="checksum mismatch"):
        installer.download_verified("https://example.com/release.jar", jar, "0" * 64)
    assert jar.read_bytes() == b"existing release"
    assert list(tmp_path.iterdir()) == [jar]
