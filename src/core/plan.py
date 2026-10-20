"""Immutable preview candidates; execution must never silently rescan."""
from dataclasses import dataclass, field
from pathlib import Path
import uuid
from .safety import identity


@dataclass(frozen=True)
class Candidate:
    path: Path
    reason: str
    size: int
    category: str
    owner: str
    risk: str
    last_used: float
    fingerprint: tuple

    @classmethod
    def capture(cls, path, reason, size, category='file', owner='', risk='review'):
        st = path.lstat()
        return cls(path, reason, size, category, owner, risk,
                   max(st.st_atime, st.st_mtime), identity(path))

    def validate(self):
        if identity(self.path) != self.fingerprint or self.path.lstat().st_atime > self.last_used:
            raise ValueError(f'changed since preview: {self.path}')


@dataclass(frozen=True)
class OperationPlan:
    action: str
    items: tuple[Candidate, ...] = ()
    commands: tuple[tuple[str, ...], ...] = ()
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    diagnostics: tuple[tuple[str, int], ...] = ()

    prepare_commands: tuple[tuple[str, ...], ...] = ()
    process_targets: tuple[str, ...] = ()
    directories: tuple[tuple[str, int, int], ...] = ()

    @property
    def bytes(self):
        return sum(item.size for item in self.items)
