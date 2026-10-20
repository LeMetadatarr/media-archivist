"""Guards against AGENTS.md inventory drift.

Parses the module and CLI-subcommand lists out of AGENTS.md's marker-delimited
sections and cross-checks each entry against the real repository tree and the
real argparse parser.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_MD = REPO_ROOT / "AGENTS.md"

_MODULE_SECTION_RE = re.compile(
    r"<!-- module-inventory -->(.*?)<!-- /module-inventory -->", re.DOTALL
)
_CLI_SECTION_RE = re.compile(
    r"<!-- cli-subcommand-inventory -->(.*?)<!-- /cli-subcommand-inventory -->",
    re.DOTALL,
)
_MODULE_PATH_RE = re.compile(r"`(media_archivist/[a-zA-Z0-9_/]+\.py)`")
_CLI_NAME_RE = re.compile(r"^-\s+`([a-z-]+)`", re.MULTILINE)


def _extract_modules() -> list[str]:
    text = AGENTS_MD.read_text(encoding="utf-8")
    match = _MODULE_SECTION_RE.search(text)
    assert match, "AGENTS.md is missing the <!-- module-inventory --> block"
    return _MODULE_PATH_RE.findall(match.group(1))


def _extract_subcommands() -> list[str]:
    text = AGENTS_MD.read_text(encoding="utf-8")
    match = _CLI_SECTION_RE.search(text)
    assert match, "AGENTS.md is missing the <!-- cli-subcommand-inventory --> block"
    return _CLI_NAME_RE.findall(match.group(1))


def test_agents_md_modules_exist():
    modules = _extract_modules()
    assert modules, "no modules parsed out of AGENTS.md's module-inventory block"
    for rel_path in modules:
        assert (REPO_ROOT / rel_path).is_file(), f"{rel_path} listed in AGENTS.md does not exist"


def test_agents_md_subcommands_exist():
    subcommands = _extract_subcommands()
    assert subcommands, "no subcommands parsed out of AGENTS.md's cli-subcommand-inventory block"

    registered = _registered_subcommands()

    for name in subcommands:
        assert name in registered, f"{name} listed in AGENTS.md is not a registered CLI subcommand"


def _registered_subcommands() -> set[str]:
    from media_archivist.cli import build_parser

    parser = build_parser()
    subparsers_action = next(
        action
        for action in parser._subparsers._group_actions
        if action.choices is not None
    )
    return set(subparsers_action.choices.keys())


def _real_modules() -> list[str]:
    """Every real, documentable module under media_archivist/.

    Mirrors the module-inventory convention: every ``.py`` file is listed,
    including package ``__init__.py`` files, except modules whose name
    starts with ``_`` (private helpers such as ``commands/_helpers.py``).
    """
    modules = []
    for path in sorted((REPO_ROOT / "media_archivist").rglob("*.py")):
        if path.name.startswith("_") and path.name != "__init__.py":
            continue
        modules.append(str(path.relative_to(REPO_ROOT)).replace("\\", "/"))
    return modules


def test_every_real_module_is_documented():
    documented = set(_extract_modules())
    real = _real_modules()
    assert real, "no modules found under media_archivist/"
    missing = [m for m in real if m not in documented]
    assert not missing, f"modules missing from AGENTS.md's module-inventory block: {missing}"


def test_every_real_subcommand_is_documented():
    documented = set(_extract_subcommands())
    registered = _registered_subcommands()
    missing = sorted(registered - documented)
    assert not missing, f"CLI subcommands missing from AGENTS.md's cli-subcommand-inventory block: {missing}"
