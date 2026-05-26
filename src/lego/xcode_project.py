from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Iterable

log = logging.getLogger(__name__)


class XcodeProjectError(RuntimeError):
    """Raised when we can't load the .xcodeproj or the target is missing."""


def _default_loader(pbxproj_path: str):
    from pbxproj import XcodeProject
    return XcodeProject.load(pbxproj_path)


def add_files_to_target(
    xcodeproj_path: Path,
    target_name: str,
    file_paths: Iterable[Path],
    project_loader: Callable | None = None,
) -> list[Path]:
    """Register the given files in `target_name` and save the .pbxproj.

    Returns the files that were actually added (already-present files are skipped).
    Raises XcodeProjectError if the project or target can't be found.
    """
    pbxproj_path = Path(xcodeproj_path) / "project.pbxproj"
    if not pbxproj_path.exists():
        raise XcodeProjectError(f"project.pbxproj not found at {pbxproj_path}")

    loader = project_loader or _default_loader
    project = loader(str(pbxproj_path))

    target_names = [t.name for t in project.objects.get_targets()]
    if target_name not in target_names:
        raise XcodeProjectError(
            f"target {target_name!r} not in xcodeproj (available: {target_names})"
        )

    files = [Path(f) for f in file_paths]
    added: list[Path] = []
    for file_path in files:
        if not file_path.exists():
            log.warning("skipping non-existent file: %s", file_path)
            continue
        result = project.add_file(str(file_path), target_name=target_name, force=False)
        if result:
            added.append(file_path)
            log.info("added %s to target %s", file_path.name, target_name)
        else:
            log.info("file already present in target %s: %s", target_name, file_path.name)

    if added:
        project.save()
    return added
