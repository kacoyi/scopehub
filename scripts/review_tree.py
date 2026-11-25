"""Read the project records of one exact Git commit without checking it out."""

from dataclasses import dataclass, field, replace
from pathlib import Path, PurePath, PurePosixPath

if __package__:
    from .verification_scope import exact_commit, git, nul_fields
else:
    from verification_scope import exact_commit, git, nul_fields


@dataclass(frozen=True)
class CommittedPath:
    """Read-only path operations used by the project loader and source lookup."""

    checkout: Path
    commit: str
    entries: dict = field(repr=False, compare=False)
    blobs: dict = field(repr=False, compare=False)
    relative: PurePosixPath = PurePosixPath('.')

    @classmethod
    def at(cls, checkout, requested):
        commit = exact_commit(checkout, requested)
        entries = {PurePosixPath('.'): (b'tree', b'')}
        for line in nul_fields(git(checkout, 'ls-tree', '-r', '-t', '-z', '--full-tree', commit)):
            identity, name = line.split(b'\t', 1)
            mode, kind, object_id = identity.split()
            if mode in (b'100644', b'100755', b'040000'):
                entries[PurePosixPath(name.decode('utf-8'))] = kind, object_id
        return cls(checkout, commit, entries, {})

    def __truediv__(self, part):
        path = self.relative / PurePath(part).as_posix()
        if path.is_absolute() or '..' in path.parts:
            raise ValueError(f'Expected a committed repository-relative path: {part}')
        return replace(self, relative=path)

    def __str__(self):
        return f'{self.commit}:{self.relative.as_posix()}'

    @property
    def name(self):
        return self.relative.name

    def resolve(self):
        return self  # Only tree entries and regular blobs are navigable.

    def relative_to(self, other):
        if (self.checkout, self.commit) != (other.checkout, other.commit):
            raise ValueError('Expected paths in the same committed tree')
        return self.relative.relative_to(other.relative)

    def is_relative_to(self, other):
        return ((self.checkout, self.commit) == (other.checkout, other.commit)
                and self.relative.is_relative_to(other.relative))

    def exists(self):
        return self.relative in self.entries

    def is_file(self):
        return self.entries.get(self.relative, (None,))[0] == b'blob'

    def glob(self, pattern):
        for path in self.entries:
            if path != self.relative and path.parent == self.relative and path.match(pattern):
                yield replace(self, relative=path)

    def read_bytes(self):
        if not self.is_file():
            raise FileNotFoundError(f'No regular committed file at {self}')
        _, object_id = self.entries[self.relative]
        if object_id not in self.blobs:
            self.blobs[object_id] = git(self.checkout, 'cat-file', 'blob', object_id.decode('ascii'))
        return self.blobs[object_id]

    def read_text(self, *, encoding):
        return self.read_bytes().decode(encoding)
