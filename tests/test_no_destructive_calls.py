from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "spacefinder"
# fsops — единственная точка удаления. trash.py дополнительно убирает
# собственные метаданные и недописанные копии внутри корзины.
ALLOWED = {"fsops.py", "trash.py"}
FORBIDDEN = (
    "shutil.rmtree(",
    "shutil.move(",
    "os.remove(",
    "os.unlink(",
    ".unlink(",
    ".rmdir(",
)


def test_destructive_calls_stay_in_controlled_modules() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if path.name in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in FORBIDDEN:
            if needle in text:
                offenders.append(f"{path.relative_to(SRC)}: {needle}")

    assert offenders == []
