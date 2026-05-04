"""Custom Faker providers for beekeeping-specific synthetic data.

All providers are deterministic when Faker is seeded (`Faker(seed=42)`).
"""
# pyright: reportMissingImports=false
from __future__ import annotations

from faker.providers import BaseProvider


QUEEN_LINEAGES = [
    "Italian",
    "Carniolan",
    "Russian",
    "Saskatraz",
    "VSH Pol-line",
    "Buckfast",
]

HIVE_FORMATS = [
    "Langstroth 8-frame",
    "Langstroth 10-frame",
    "Langstroth double-deep",
    "Top-bar",
]

HONEY_VARIETALS = [
    "wildflower",
    "clover",
    "orange-blossom",
    "sage",
    "buckwheat",
    "tupelo",
    "alfalfa",
    "sourwood",
    "macadamia-blossom",
    "manuka-style",
]

CROPS = [
    "almond",
    "apple",
    "cherry",
    "pear",
    "blueberry",
    "cranberry",
    "melon",
    "pumpkin",
    "cucumber",
    "citrus",
]

# Built-in messy-name variants for the fuzzy-match demo.
# Each tuple is (canonical, list of alias forms).
EDGE_CASE_CUSTOMERS = [
    (
        "O'Reilly Family Orchards",
        ["O'Reilly's Almonds", "OReilly Orchard", "O'Reilly Family Farm LLC"],
    ),
    (
        "Cascade Orchard Partners",
        ["Cascade Orchards LP", "Cascade Orch. Partners"],
    ),
    (
        "Five Rivers Apiaries",
        ["5 Rivers Apiaries", "Five Rivers Bee Co."],
    ),
    (
        "St. Agnes Pollination",
        ["Saint Agnes Pollination", "St Agnes Pollination Co."],
    ),
    (
        "Martinez & Sons Orchards",
        ["Martinez and Sons Orchards", "Martinez & Sons LLC"],
    ),
]


class BeekeepingProvider(BaseProvider):
    """Faker provider for beekeeping-specific terms."""

    def queen_lineage(self) -> str:
        return self.random_element(QUEEN_LINEAGES)

    def hive_format(self) -> str:
        return self.random_element(HIVE_FORMATS)

    def honey_varietal(self) -> str:
        return self.random_element(HONEY_VARIETALS)

    def crop(self) -> str:
        return self.random_element(CROPS)

    def orchard_name(self) -> str:
        """A synthetic orchard-style customer name."""
        family_names = [
            "Haley",
            "Wenatchee",
            "Blue Ridge",
            "Seven Acres",
            "Foothill",
            "Mesa",
            "Mountain Valley",
            "Juniper Hill",
            "Sierra Crest",
            "Sun Valley",
            "Riverbend",
            "Goldenridge",
            "Old Mission",
            "Hidden Creek",
            "Silver Oak",
        ]
        suffixes = [
            "Orchards",
            "Family Orchards",
            "Farm",
            "Farms",
            "Orchard Co.",
            "Growers",
            "Fruit Co.",
            "Orchards LLC",
            "Farms LP",
            "Orchard Partners",
        ]
        return f"{self.random_element(family_names)} {self.random_element(suffixes)}"

    def wholesaler_name(self) -> str:
        stems = [
            "Sierra Raw Honey",
            "Northern Lights",
            "Pacific Honey",
            "Golden West",
            "Mountain Comb",
            "Heartland Apiary",
            "Cascadia Honey",
        ]
        suffixes = ["Co.", "Distributors", "Wholesale", "Trading Co.", "Partners"]
        return f"{self.random_element(stems)} {self.random_element(suffixes)}"

    def specialty_name(self) -> str:
        stems = [
            "Artisan Ferment & Food",
            "Old Sonoma Honey Market",
            "Hivecraft Provisions",
            "Beelore & Co.",
            "Urban Hive Pantry",
            "Honeywell Provisions",
        ]
        return self.random_element(stems)

    def meadery_name(self) -> str:
        stems = [
            "Stormchaser Meadery",
            "North Wind Mead Co.",
            "Drakestone Mead",
            "Amberlight Meadery",
        ]
        return self.random_element(stems)

    def peer_beekeeper_name(self) -> str:
        stems = [
            "Five Rivers Apiaries",
            "Cobblestone Hives",
            "Juniper Bee Co.",
            "Broken Arrow Bees",
            "Sun Meridian Apiaries",
        ]
        return self.random_element(stems)
