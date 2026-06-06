import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.text_processing import parse_quantity


@pytest.mark.parametrize(
    "text,want",
    [
        ("4k", 4000),
        ("4к", 4000),
        ("4 тыс", 4000),
        ("3,5k", 3500),
        ("~4.2k", 4200),
        ("4000 pcs", 4000),
        ("5 000", 5000),
        ("≈5000", 5000),
        ("тел 89001234567", None),
    ],
)
def test_parse_quantity_v15(text, want):
    assert parse_quantity(text) == want
