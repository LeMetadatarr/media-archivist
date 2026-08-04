"""Behavior-preserving net for the CLI parser → handler wiring.

`cli.py` grew to a single ~965-line module. Before/after splitting the command
handlers into per-theme modules this test pins down two invariants that a pure
code move must not change:

1. Every registered subcommand dispatches to a handler of the same name
   (snapshot below). Moving `cmd_x` between modules keeps ``func.__name__``,
   so a move that accidentally rewires a command shows up here.
2. ``<subcommand> --help`` exits cleanly (0) for every subcommand — i.e. every
   subparser is reachable and well-formed.
"""
from __future__ import annotations

import argparse

import pytest

from media_archivist.cli import build_parser, main


def _subcommand_actions(parser: argparse.ArgumentParser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    raise AssertionError("no subparsers found on the CLI parser")


# subcommand name -> handler function name. This is the contract; a code move
# must leave it byte-for-byte identical.
EXPECTED_DISPATCH = {
    "add": "cmd_add",
    "urls": "cmd_urls",
    "list": "cmd_list",
    "dump": "cmd_dump",
    "export": "cmd_export",
    "import": "cmd_import",
    "merge": "cmd_merge",
    "stats": "cmd_stats",
    "prune": "cmd_prune",
    "bootstrap": "cmd_bootstrap",
    "strm-export": "cmd_strm_export",
    "serve": "cmd_serve",
    "discover": "cmd_discover",
    "sync": "cmd_sync",
    "enrich": "cmd_enrich",
    "snapshot": "cmd_snapshot",
    "diff": "cmd_diff",
    "hub-publish": "cmd_hub_publish",
    "providers": "cmd_providers",
    "canonicalize": "cmd_canonicalize",
    "entities-list": "cmd_entities_list",
    "entities-show": "cmd_entities_show",
    "entities-stats": "cmd_entities_stats",
    "quarantine-list": "cmd_quarantine_list",
    "quarantine-resolve": "cmd_quarantine_resolve",
    "quarantine-reject": "cmd_quarantine_reject",
    "link": "cmd_link",
    "dedupe": "cmd_dedupe",
    "monitor": "cmd_monitor",
    "tag-library": "cmd_tag_library",
    "resolve": "cmd_resolve",
    "download": "cmd_download",
}


def test_every_subcommand_dispatches_to_expected_handler():
    choices = _subcommand_actions(build_parser())
    actual = {}
    for name, subparser in choices.items():
        func = subparser.get_default("func")
        assert func is not None, f"subcommand {name!r} has no func default"
        assert callable(func), f"subcommand {name!r} func is not callable"
        actual[name] = func.__name__
    assert actual == EXPECTED_DISPATCH


@pytest.mark.parametrize("subcommand", sorted(EXPECTED_DISPATCH))
def test_subcommand_help_exits_clean(subcommand):
    with pytest.raises(SystemExit) as exc:
        main([subcommand, "--help"])
    assert exc.value.code == 0
