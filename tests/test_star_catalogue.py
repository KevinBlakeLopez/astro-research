"""The fixed-star catalogue, as a list to find stars in.

`Ephemeris.star_catalogue()` reads the verified folder's sefstars.txt and lists each name and
designation once, in the order they first appear, with the star's magnitude. The file repeats
some records exactly (Regulus is on two lines) and gives some stars more than one name
(Aldebaran and Rohini are both alTau), so names and designations don't match one to one.
Each entry carries the `FixedStar` the paran functions take.
"""

import shutil
from pathlib import Path

import pytest

from astro_research.ephemeris import Ephemeris, StaleEphemeris, load_manifest, verify_ephemeris
from astro_research.parans import CatalogueStar, FixedStar


@pytest.fixture
def eph(fetched_ephe: Path) -> Ephemeris:
    return Ephemeris.open(fetched_ephe)


def test_each_name_and_designation_is_listed_once_in_file_order(eph: Ephemeris) -> None:
    catalogue = eph.star_catalogue()
    # The pinned file: 1,360 records, 1,338 distinct name-designation pairs, 1,120 designations.
    assert len(catalogue) == 1338
    assert len({(entry.name, entry.star) for entry in catalogue}) == 1338
    assert len({entry.star for entry in catalogue}) == 1120
    assert catalogue[:3] == (
        CatalogueStar(name="Aldebaran", star=FixedStar(designation=",alTau"), magnitude=0.86),
        CatalogueStar(name="Rohini", star=FixedStar(designation=",alTau"), magnitude=0.86),
        CatalogueStar(name="Algol", star=FixedStar(designation=",bePer"), magnitude=2.12),
    )


def test_names_lose_the_file_s_padding_and_a_repeated_record_is_listed_once(
    eph: Ephemeris,
) -> None:
    # "Regulus    ,alLeo" is on two lines of the file; Magha is alLeo's other name.
    leo = [entry for entry in eph.star_catalogue() if entry.star.designation == ",alLeo"]
    assert leo == [
        CatalogueStar(name="Regulus", star=FixedStar(designation=",alLeo"), magnitude=1.4),
        CatalogueStar(name="Magha", star=FixedStar(designation=",alLeo"), magnitude=1.4),
    ]


def test_swiss_ephemeris_finds_every_catalogue_star(eph: Ephemeris) -> None:
    # star_name gives the designation's first name: Rohini's star is "Aldebaran,alTau". One
    # designation comes back as another: zePscA as "Revati,zePsc", a record with the same
    # coordinates (observed). Swiss Ephemeris also finds zePscB, which the file doesn't have,
    # so callers check designations against the catalogue, not by looking them up.
    other = {}
    for entry in eph.star_catalogue():
        found = eph.star_name(entry.star).split(",", 1)[1]
        if f",{found}" != entry.star.designation:
            other[entry.star.designation] = found
    assert other == {",zePscA": "zePsc"}


def test_the_catalogue_is_the_verified_folder_s(fetched_ephe: Path, tmp_path: Path) -> None:
    eph = Ephemeris.open(fetched_ephe)
    copy = tmp_path / "ephe"
    copy.mkdir()
    for file in load_manifest().files:
        shutil.copy2(fetched_ephe / file.name, copy / file.name)
    verify_ephemeris(copy)  # the process-wide path moves, so the first handle is stale
    with pytest.raises(StaleEphemeris):
        eph.star_catalogue()
