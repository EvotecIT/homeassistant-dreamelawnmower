"""Regression checks for the read-only cloud property probe CLI helpers."""

from __future__ import annotations

import argparse

import pytest

from examples.property_probe import _parse_csv_numbers, _property_keys


def test_parse_csv_numbers_uses_default_for_empty_input() -> None:
    assert _parse_csv_numbers("", default=(1, 2, 6)) == [1, 2, 6]
    assert _parse_csv_numbers(None, default=(3,)) == [3]


def test_property_keys_prefers_explicit_keys() -> None:
    args = argparse.Namespace(
        keys="2.1, 2.2,6.13",
        piid_end=99,
        piid_start=1,
        siids="1,2,6",
    )

    assert _property_keys(args) == ["2.1", "2.2", "6.13"]


def test_property_keys_expands_siids_and_piids() -> None:
    args = argparse.Namespace(
        keys="",
        piid_end=3,
        piid_start=2,
        siids="1,6",
    )

    assert _property_keys(args) == ["1.2", "1.3", "6.2", "6.3"]


def test_property_keys_rejects_invalid_range() -> None:
    args = argparse.Namespace(
        keys="",
        piid_end=1,
        piid_start=2,
        siids="1",
    )

    with pytest.raises(ValueError, match="--piid-end"):
        _property_keys(args)
