from __future__ import annotations

import os
from pathlib import Path

from spacefinder.core.format import display_path, human_bytes
from spacefinder.core.scanner import measure_path


def test_human_bytes() -> None:
    assert human_bytes(0) == "0 Б"
    assert human_bytes(1023) == "1023 Б"
    assert human_bytes(1024) == "1,0 КБ"
    assert human_bytes(1024 * 1024) == "1,0 МБ"
    assert human_bytes(1024**3) == "1,0 ГБ"


def test_display_path(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert display_path(home, home) == "~"
    assert display_path(home / ".cache" / "pip", home) == "~/.cache/pip"
    assert display_path(Path("/var/cache"), home) == "/var/cache"


def test_measure_file(tmp_path: Path) -> None:
    path = tmp_path / "blob.bin"
    path.write_bytes(b"x" * 5000)

    disk_bytes, apparent_bytes = measure_path(path)

    assert apparent_bytes == 5000
    assert disk_bytes == path.stat().st_blocks * 512


def test_measure_symlink_is_not_followed(tmp_path: Path) -> None:
    target = tmp_path / "target.bin"
    target.write_bytes(b"x" * 10_000)
    link = tmp_path / "link"
    link.symlink_to(target)

    warnings: list[str] = []
    disk_bytes, apparent_bytes = measure_path(link, warnings=warnings)

    assert disk_bytes == 0
    assert apparent_bytes == 0
    assert any("ссылка" in warning for warning in warnings)


def test_measure_hardlink_counted_once(tmp_path: Path) -> None:
    directory = tmp_path / "dir"
    directory.mkdir()
    first = directory / "a.bin"
    first.write_bytes(b"x" * 5000)
    second = directory / "b.bin"
    os.link(first, second)

    warnings: list[str] = []
    disk_bytes, apparent_bytes = measure_path(directory, warnings=warnings)

    assert apparent_bytes == 5000
    assert disk_bytes == first.stat().st_blocks * 512
    assert any("Жёсткая ссылка" in warning for warning in warnings)


def test_measure_directory_skips_symlink_inside(tmp_path: Path) -> None:
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"y" * 9000)
    directory = tmp_path / "dir"
    directory.mkdir()
    (directory / "real.bin").write_bytes(b"x" * 3000)
    (directory / "link").symlink_to(outside)

    warnings: list[str] = []
    _, apparent_bytes = measure_path(directory, warnings=warnings)

    assert apparent_bytes == 3000
    assert any("ссылка" in warning for warning in warnings)
