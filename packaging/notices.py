"""Writes the licence texts of everything the exe bundles, for publishing next to it.

Run with the interpreter the exe is built from:
    python packaging\\notices.py dist\\THIRD-PARTY-NOTICES.txt
"""

import importlib.metadata as metadata
import re
import sys
from pathlib import Path

from packaging.requirements import Requirement

ROOT = "power-meter"
RULE = "=" * 78


def runtime_distributions() -> list[metadata.Distribution]:
    """The app's run-time dependencies and theirs, without extras."""
    seen: dict[str, metadata.Distribution] = {}
    pending = [ROOT]
    while pending:
        dist = metadata.distribution(pending.pop())
        for spec in dist.requires or []:
            requirement = Requirement(spec)
            if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
                continue
            name = requirement.name.lower()
            if name not in seen:
                seen[name] = metadata.distribution(name)
                pending.append(name)
    return sorted(seen.values(), key=lambda d: d.metadata["Name"].lower())


def licence_texts(dist: metadata.Distribution) -> list[str]:
    texts = [
        Path(str(dist.locate_file(file))).read_text(encoding="utf-8", errors="replace")
        for file in dist.files or []
        if ".dist-info/" in str(file)
        and re.search(r"/licen[cs]es/|/(licen[cs]e|copying|notice)[^/]*$", str(file), re.I)
    ]
    if texts:
        return texts
    # nvidia-ml-py ships its BSD licence only as the header of pynvml.py.
    for file in dist.files or []:
        if str(file).endswith(".py"):
            source = Path(str(dist.locate_file(file))).read_text(encoding="utf-8")
            header = re.match(r"#+\n((?:#.*\n)+?)#+\n", source)
            if header and "Copyright" in header.group(1):
                return [re.sub(r"^# ?", "", header.group(1), flags=re.M)]
    raise SystemExit(f"No licence text found for {dist.metadata['Name']}")


def section(title: str, body: str) -> str:
    return f"{RULE}\n{title}\n{RULE}\n\n{body.strip()}\n\n"


def main(out: Path) -> None:
    # The Windows installer's LICENSE.txt also covers what CPython bundles, Tcl/Tk among them.
    python_licence = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_licence.is_file():
        raise SystemExit(f"Python's licence file is missing: {python_licence}")
    parts = [
        "PC Power & Cost Meter bundles the software below. Each part keeps its own licence.\n\n",
        section(f"Python {sys.version.split()[0]}", python_licence.read_text(encoding="utf-8")),
    ]
    for dist in runtime_distributions():
        title = f"{dist.metadata['Name']} {dist.version}"
        parts.append(section(title, "\n\n".join(licence_texts(dist))))
    out.write_text("".join(parts), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
