import logging
import pathlib

import click
import rdflib

from pubmate.cli._signing import resolve_signing
from pubmate.defining import DefiningNanopubBuilder
from pubmate.idmap import IdMap
from pubmate.incremental import publish_incremental
from pubmate.minting import SequentialMinter, term_input_from_assertion
from pubmate.supersede import SupersessionBuilder
from pubmate.utils import serialize_nanopub

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@click.command()
@click.option("--assertion-folder", "-a", required=True, type=click.Path(exists=True, file_okay=False, path_type=pathlib.Path))
@click.option("--namespace", default="https://w3id.org/peh/biochementities/", show_default=True)
@click.option("--output-dir", required=True, type=click.Path(file_okay=False, path_type=pathlib.Path), help="Where to write the minted .trig nanopubs.")
@click.option("--id-map-file", type=click.Path(dir_okay=False, path_type=pathlib.Path), help="TSV id-map to write/merge (old_id -> thing_uri, np_uri).")
@click.option("--default-suggester", help="Fallback suggester ORCID for terms without their own.")
@click.option("--part-of", help="URI each term links to via dcterms:isPartOf in its assertion (e.g. the vocabulary).")
@click.option("--nanopub-type", "nanopub_types", multiple=True, help="URI tagged in pubinfo as npx:hasNanopubType on every nanopub (repeatable).")
@click.option("--template", help="Assertion-template URI tagged in pubinfo as nt:wasCreatedFromTemplate on every nanopub (for Nanodash rendering).")
@click.option("--orcid-id")
@click.option("--name")
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
    default_suggester: str | None,
    part_of: str | None,
    nanopub_types: tuple[str, ...],
    template: str | None,
    orcid_id: str | None,
    name: str | None,
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
    """Incrementally mint/supersede defining nanopubs from per-term assertions.

    Each assertion is re-keyed onto the artifact-code placeholder. Per term, this
    compares its identity fingerprint against the one recorded in --id-map-file:
    a new term is minted, an unchanged term is skipped, and a *drifted* term
    (content or wrapper changed) is superseded -- re-stated against its existing
    thing URI in a nanopub that supersedes the recorded one, keeping the term's
    identity. Published nanopubs (unless --dry-run) are written to --output-dir as
    <artifact-code>.trig and the updated old_id -> thing_uri/np_uri/fingerprint
    map is written to --id-map-file.

    Inter-term links (forward refs/cycles) are intentionally left to the migration
    superseding pass (see the migration tooling); this mints the assertions as
    given.
    """
    signing = resolve_signing(
        orcid_id=orcid_id,
        name=name,
        private_key=private_key,
        public_key=public_key,
        intro_nanopub_uri=intro_nanopub_uri,
        use_testsuite_keys=use_testsuite_keys,
        testsuite_key=testsuite_key,
        testsuite_ref=testsuite_ref,
        test_server=test_server,
        dry_run=dry_run,
    )
    builder = DefiningNanopubBuilder(
        namespace, profile=signing.profile, test_server=signing.test_server,
        nanopub_types=nanopub_types, template=template,
    )
    supersession_builder = SupersessionBuilder(
        profile=signing.profile, test_server=signing.test_server,
        license=builder.license, nanopub_types=nanopub_types, template=template,
    )

    files = sorted(assertion_folder.glob(pattern))
    if not files:
        logger.info("No assertions matching %s in %s. Nothing to mint.", pattern, assertion_folder)
        return

    terms = []
    for path in files:
        graph = rdflib.Graph()
        graph.parse(path, format="turtle")
        terms.append(
            term_input_from_assertion(
                graph,
                namespace=namespace,
                thing_uri=builder.thing_uri,
                default_suggester=default_suggester,
                part_of=part_of,
            )
        )

    existing = IdMap.from_tsv(id_map_file.read_text(encoding="utf-8")) if id_map_file and id_map_file.exists() else IdMap()

    minter = SequentialMinter(builder, default_suggester_orcid=default_suggester)
    result = publish_incremental(
        terms,
        minter=minter,
        supersession_builder=supersession_builder,
        existing=existing,
        dry_run=dry_run,
    )

    # Write each minted/superseding nanopub as <artifact-code>.trig (its own code:
    # for a defining nanopub that equals the thing code, for a supersession its own).
    output_dir.mkdir(parents=True, exist_ok=True)
    published = [(m.np_uri, m.nanopub) for m in result.minted.terms]
    published += [(s.np_uri, s.nanopub) for s in result.superseded]
    for np_uri, np in published:
        code = np_uri.rsplit("/", 1)[-1]
        (output_dir / f"{code}.trig").write_text(serialize_nanopub(np), encoding="utf-8")

    if id_map_file is not None:
        id_map_file.parent.mkdir(parents=True, exist_ok=True)
        result.id_map.write_tsv(id_map_file)
        logger.info("Wrote id-map (%d entries) -> %s", len(result.id_map), id_map_file)

    logger.info(
        "Minted %d, superseded %d, skipped %d term(s)%s -> %s",
        len(result.minted.terms), len(result.superseded), len(result.skipped),
        " (dry-run)" if dry_run else "", output_dir,
    )


if __name__ == "__main__":
    cli()
