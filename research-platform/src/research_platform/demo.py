"""The Gauquelin demo: the Open Gauquelin Database's group A1, loaded into an empty platform.

The desktop app offers it while there are no records, so a tester has something to explore.
The data isn't shipped with the app: `load_gauquelin` downloads it from opengauquelin.org when
asked, so each tester gets it from its source, under the site's own license. In order, it:

1. downloads the group's zip, with `fetch_https` in the app;
2. checks its SHA-256 against the one this version was made for, so every tester gets the same
   records, and refuses a file that has changed since;
3. takes the zip's one file, A1.csv, and converts it with `open_gauquelin`, with seed 1 for the
   controls;
4. in one transaction, makes the category variable `group` (champion, control) unless it's
   already so, a source for the champions and one for the controls, and the dataset
   "Gauquelin A1", and imports both files through the CSV import's own checks.

Anything refused, a row the import refuses included, raises DemoRefused and changes nothing.
"""

import hashlib
import io
import ssl
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from importlib.metadata import version

import truststore
from django.db import transaction
from django.utils import timezone

from research_platform import open_gauquelin
from research_platform.csv_import import counted, plan_import, save
from research_platform.models import Category, Dataset, Record, Source, Variable


@dataclass(frozen=True, kw_only=True)
class DemoFile:
    url: str
    sha256: str  # hex
    member: str  # the zip's one file
    dated: date  # the file's own date


A1 = DemoFile(
    url="https://opengauquelin.org/download/history/1970-1984-gauquelin/A1.csv.zip",
    sha256="5d8b245ef531d950471df0d15fce18b79710f0bdea82527c2e553ebe0be13768",
    member="A1.csv",
    dated=date(2024, 1, 11),
)
DATASET = "Gauquelin A1"
SEED = 1
LARGEST = 2**20  # the most bytes a download may have; A1's zip has 79,707
TIMEOUT = 30  # seconds
LICENSE = "https://opengauquelin.org/about"  # where the site gives its data's license
GROUP = "group"
CATEGORIES = ("champion", "control")


class DemoRefused(ValueError):
    """Why the demo wasn't loaded; nothing was changed."""


@dataclass(frozen=True)
class Loaded:
    dataset: Dataset
    champions: int
    controls: int
    left_out: int


def load_gauquelin(fetch: Callable[[str], bytes], file: DemoFile = A1) -> Loaded:
    """The demo's dataset, made from `file` as `fetch` downloads it; raises DemoRefused."""
    _check_ready()  # before downloading anything
    content = fetch(file.url)
    if hashlib.sha256(content).hexdigest() != file.sha256:
        raise DemoRefused(
            f"the file at {file.url} has changed since this version of the app was made for "
            "it, so the demo wasn't loaded"
        )
    text = _member(content, file.member)
    try:
        conversion = open_gauquelin.convert(open_gauquelin.read_text(text))
        made = open_gauquelin.controls(conversion.champions, SEED)
    except ValueError as error:  # SourceRefused, or too few champions
        raise DemoRefused(f"{file.member} can't be converted: {error}") from None
    with transaction.atomic():  # a DemoRefused raised in here keeps none of it
        _group()
        champions = Source.objects.create(
            citation=(
                f"Open Gauquelin Database, group A1: sports champions, the file of "
                f"{file.dated.isoformat()} ({file.url})"
            ),
            retrieved_on=timezone.localdate(),
            notes=(
                f"Downloaded by the desktop app's demo, SHA-256 {file.sha256}. The site gives "
                f"its data's license at {LICENSE}."
            ),
        )
        controls = Source.objects.create(
            citation=(
                "Shuffled controls for the Open Gauquelin Database's group A1, made by "
                f"research_platform.open_gauquelin {version('research-platform')} with seed {SEED}"
            ),
            notes=(
                "Each control keeps a champion's date, offset and place, and takes another "
                "champion's clock time (see research_platform.open_gauquelin)."
            ),
        )
        dataset = Dataset.objects.create(
            name=DATASET,
            description=(
                "The Open Gauquelin Database's sports champions (group A1), and a shuffled "
                "control for each, loaded by the desktop app's demo."
            ),
        )
        for source, births, category in (
            (champions, conversion.champions, "champion"),
            (controls, made, "control"),
        ):
            rows = open_gauquelin.import_rows(births, category)
            text = open_gauquelin.csv_text(open_gauquelin.IMPORT_COLUMNS, rows)
            plan = plan_import(text, source, add_to_existing=False)
            if plan.refused:
                line, why = next(iter(plan.refused.items()))
                raise DemoRefused(
                    f"the import refused {counted(len(plan.refused), 'row')} of the "
                    f"{category}s, so the demo wasn't loaded; the first, line {line}: {why}"
                )
            save(plan, source, dataset)
    return Loaded(dataset, len(conversion.champions), len(made), len(conversion.left_out))


def fetch_https(url: str) -> bytes:
    """The body at `url`, checked against the Mac's own trusted certificates; raises
    DemoRefused if it can't be downloaded or is larger than LARGEST."""
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # verifies, and checks the name
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT, context=context) as response:
            body: bytes = response.read(LARGEST + 1)
    except OSError as error:  # URLError, a timeout or a refused connection
        raise DemoRefused(f"couldn't download {url}: {getattr(error, 'reason', error)}") from None
    if len(body) > LARGEST:
        raise DemoRefused(f"{url} is more than {LARGEST // 2**20} MiB, which the demo's file isn't")
    return body


def _check_ready() -> None:
    """Refuses unless the platform is empty and the demo's names are free or as it needs."""
    if Record.objects.exists():
        raise DemoRefused("the platform already has records; the demo is for an empty one")
    if Dataset.objects.filter(name=DATASET).exists():
        raise DemoRefused(f"a dataset named {DATASET} already exists")
    group = Variable.objects.filter(name=GROUP).first()
    if group is not None:
        names = set(group.categories.values_list("name", flat=True))
        if group.kind != Variable.Kind.CATEGORY or not set(CATEGORIES) <= names:
            raise DemoRefused(
                f"a variable named {GROUP} already exists, and isn't a category with "
                f"{' and '.join(CATEGORIES)}, which the demo needs"
            )


def _group() -> None:
    """The `group` variable, made unless `_check_ready` found it already as it's needed."""
    if Variable.objects.filter(name=GROUP).exists():
        return
    group = Variable.objects.create(
        name=GROUP,
        kind=Variable.Kind.CATEGORY,
        description="Whether a subject is one of the Gauquelins' champions or a control.",
    )
    for name in CATEGORIES:
        Category.objects.create(variable=group, name=name)


def _member(content: bytes, member: str) -> str:
    """The text of the zip's one file, `member`; raises DemoRefused for anything else."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise DemoRefused("the download isn't a zip file") from None
    with archive:
        names = archive.namelist()
        if names != [member]:
            held = ", ".join(names) or "nothing"
            raise DemoRefused(f"the zip should hold {member} alone, and holds {held}")
        data = archive.read(member)
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DemoRefused(f"{member} isn't UTF-8") from None
