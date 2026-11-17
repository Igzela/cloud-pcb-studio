"""Persist actual router subprocess evidence, including partial timeout output."""
from pathlib import Path


def _text(value):
    if value is None:
        return ''
    return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else str(value)


def save_router_log(path, command, returncode, stdout=None, stderr=None, *, timed_out=False):
    path = Path(path)
    text = _text(stdout) + '\n' + _text(stderr)
    if timed_out:
        text += '\n[runner: process timed out; partial output preserved]\n'
    path.write_text(text, encoding='utf-8')
    return dict(command=[str(c) for c in command], returncode=None if timed_out else returncode,
                timed_out=timed_out, log=path.name)
