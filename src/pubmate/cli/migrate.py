"""CLI: migrate an existing cross-referencing vocabulary to nanopub ids.

Reads per-term assertion ``.ttl`` files (each keyed on the term's old URI),
mints a **defining** nanopub for each in dependency order with inter-term
references resolved inline, and publishes **superseding** nanopubs for the
cyclic links that could not be resolved at mint time. Writes every nanopub to
``--output-dir`` (named by its own artifact code) and the merged old->new
id-map to ``--id-map-file``.
"""

import logging
import pathlib

import click
import rdflib

from pubmate.cli._signing import resolve_signing
from pubmate.defining import DefiningNanopubBuilder
from pubmate.idmap import IdMap, IdMapEntry
from pubmate.migrate import migrate_terms
from pubmate.minting import SequentialMinter
from pubmate.supersede import SupersessionBuilder
from pubmate.utils import serialize_nanopub

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _term_subject(graph: rdflib.Graph, namespace: str, path: pathlib.Path) -> str:
    subjects = {s for s in graph.subjects() if isinstance(s, rdflib.URIRef) and str(s).startswith(namespace)}
    if len(subjects) != 1:
        raise click.ClickException(
            f"{path}: expected exactly one subject in namespace {namespace!r}, found {len(subjects)}."
        )
    return str(next(iter(subjects)))


def _code(np_uri: str) -> str:
    """The artifact code = last path segment of the nanopub URI (…/np/RA<code>)."""
    return np_uri.rstrip("/").rsplit("/", 1)[-1]


@click.command()
@click.option("--assertion-folder", "-a", required=True, type=click.Path(exists=True, file_okay=False, path_type=pathlib.Path))
@click.option("--namespace", default="https://w3id.org/peh/biochementities/", show_default=True)
@click.option("--output-dir", required=True, type=click.Path(file_okay=False, path_type=pathlib.Path), help="Where to write all minted .trig nanopubs (defining + superseding).")
@click.option("--id-map-file", type=click.Path(dir_okay=False, path_type=pathlib.Path), help="TSV id-map to write/merge (old_id -> thing_uri, np_uri).")
@click.option("--orcid-id")
@click.option("--name")
@click.option("--default-suggester", help="ORCID attributed as the suggester (prov:wasAttributedTo) for any term that carries none — e.g. the contributor of an existing batch being migrated.")
@click.option("--nanopub-type", "nanopub_types", multiple=True, help="URI tagged in pubinfo as npx:hasNanopubType on every nanopub (repeatable).")
@click.option("--template", help="Assertion-template URI tagged in pubinfo as nt:wasCreatedFromTemplate on every nanopub (for Nanodash rendering).")
@click.option("--part-of", help="URI each term links to via dcterms:isPartOf in its assertion (e.g. the vocabulary).")
@click.option("--private-key", type=click.Path(exists=True, dir_okay=False))
@click.option("--public-key", type=click.Path(exists=True, dir_okay=False))
@click.option("--intro-nanopub-uri")
@click.option("--test-server", is_flag=True, help="Publish to the nanopub test server (with --private-key).")
@click.option("--use-testsuite-keys", is_flag=True, help="Sign with nanopub-testsuite-connector key material (test server).")
@click.option("--testsuite-key", default="rsa-key1", show_default=True, hidden=True)
@click.option("--testsuite-ref", default="main", show_default=True, hidden=True)
@click.option("--dry-run", is_flag=True, help="Sign only (offline); do not publish to the network.")
@click.option("--glob", "pattern", default="*.ttl", show_default=True)
def cli(
    assertion_folder: pathlib.Path,
    namespace: str,
    output_dir: pathlib.Path,
    id_map_file: pathlib.Path | None,
    orcid_id: str | None,
    name: str | None,
    default_suggester: str | None,
    nanopub_types: tuple[str, ...],
    template: str | None,
    part_of: str | None,
    private_key: str | None,
    public_key: str | None,
    intro_nanopub_uri: str | None,
    test_server: bool,
    use_testsuite_keys: bool,
    testsuite_key: str,
    testsuite_ref: str,
    dry_run: bool,
    pattern: str,
) -> None:
    """Migrate a cross-referencing term set to nanopub-based identifiers.

    Acyclic inter-term references are resolved to new thing URIs inline at mint
    time; cyclic references (e.g. symmetric isIsomerOf) are added afterwards by
    superseding nanopubs. Already-minted terms (present in --id-map-file) are
    skipped, so the migration is resumable.
    """
    signing = resolve_signing(
        orcid_id=orcid_id, name=name, private_key=private_key, public_key=public_key,
        intro_nanopub_uri=intro_nanopub_uri, use_testsuite_keys=use_testsuite_keys,
        testsuite_key=testsuite_key, testsuite_ref=testsuite_ref, test_server=test_server, dry_run=dry_run,
    )
    builder = DefiningNanopubBuilder(
        namespace, profile=signing.profile, test_server=signing.test_server,
        nanopub_types=nanopub_types, template=template,
    )
    minter = SequentialMinter(builder, default_suggester_orcid=default_suggester)
    supersession = SupersessionBuilder(
        profile=signing.profile, test_server=signing.test_server,
        nanopub_types=nanopub_types, template=template,
    )

    files = sorted(assertion_folder.glob(pattern))
    if not files:
        logger.info("No assertions matching %s in %s. Nothing to migrate.", pattern, assertion_folder)
        return

    assertions: dict[str, rdflib.Graph] = {}
    for path in files:
        graph = rdflib.Graph()
        graph.parse(path, format="turtle")
        assertions[_term_subject(graph, namespace, path)] = graph

    existing = IdMap.from_tsv(id_map_file.read_text(encoding="utf-8")) if id_map_file and id_map_file.exists() else IdMap()

    result = migrate_terms(
        assertions, namespace=namespace, minter=minter, supersession_builder=supersession,
        existing=existing, dry_run=dry_run, part_of=part_of,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    for minted in result.defining.terms:
        (output_dir / f"{_code(minted.np_uri)}.trig").write_text(serialize_nanopub(minted.nanopub), encoding="utf-8")
    for sup in result.superseding:
        (output_dir / f"{_code(sup.np_uri)}.trig").write_text(serialize_nanopub(sup.nanopub), encoding="utf-8")

    if id_map_file is not None:
        id_map_file.parent.mkdir(parents=True, exist_ok=True)
        result.id_map.write_tsv(id_map_file)
        logger.info("Wrote id-map (%d entries) -> %s", len(result.id_map), id_map_file)

    logger.info(
        "Migrated%s: %d defining + %d superseding nanopub(s) (%d deferred edge(s)) -> %s",
        " (dry-run)" if dry_run else "",
        len(result.defining.terms),
        len(result.superseding),
        len(result.deferred_edges),
        output_dir,
    )


if __name__ == "__main__":
    cli()
