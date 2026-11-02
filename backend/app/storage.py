from pathlib import Path


def storage_path(root: Path, key: str) -> Path:
    root = root.resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root) or path.parent != root:
        raise ValueError('Invalid storage key')
    return path
