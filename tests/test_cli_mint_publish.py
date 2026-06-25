import rdflib
from click.testing import CliRunner
from rdflib.namespace import RDF, RDFS

from pubmate.cli.mint_publish import cli
from pubmate.idmap import IdMap
from pubmate.minting import term_input_from_assertion

NAMESPACE = "https://w3id.org/peh/biochementities/"
OLD_ID = f"{NAMESPACE}01KRB098ND0MXJ7J2ZSF49KSFN"
PLACEHOLDER = rdflib.URIRef(f"{NAMESPACE}~~~ARTIFACTCODE~~~")
SKOS = rdflib.Namespace("http://www.w3.org/2004/02/skos/core#")
PROV = rdflib.Namespace("http://www.w3.org/ns/prov#")
SUGGESTER = "https://orcid.org/0000-0002-1825-0097"


def _assertion_graph(path=None) -> rdflib.Graph:
    g = rdflib.Graph()
    s = rdflib.URIRef(OLD_ID)
    g.add((s, RDF.type, SKOS.Concept))
    g.add((s, RDFS.label, rdflib.Literal("Caffeine")))
    g.add((s, PROV.wasAttributedTo, rdflib.URIRef(SUGGESTER)))
    if path is not None:
        g.serialize(destination=path, format="turtle")
    return g


def test_term_input_rekeys_and_lifts_suggester() -> None:
    term = term_input_from_assertion(_assertion_graph(), namespace=NAMESPACE, thing_uri=PLACEHOLDER)

    assert term.term_id == OLD_ID
    assert term.suggester_orcid == SUGGESTER
    assert term.label == "Caffeine"
    # Subject is re-keyed to the placeholder; suggester is lifted out of the assertion.
    assert set(term.assertion.subjects()) == {PLACEHOLDER}
    assert (None, PROV.wasAttributedTo, None) not in term.assertion


def test_term_input_requires_single_namespaced_subject() -> None:
    g = rdflib.Graph()  # no subject in the namespace
    g.add((rdflib.URIRef("http://example.org/x"), RDFS.label, rdflib.Literal("x")))
    try:
        term_input_from_assertion(g, namespace=NAMESPACE, thing_uri=PLACEHOLDER)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for missing namespaced subject")


def test_mint_publish_dry_run_writes_trig_and_idmap(tmp_path) -> None:
    assertions = tmp_path / "assertions"
    assertions.mkdir()
    _assertion_graph(assertions / "caffeine.ttl")
    out = tmp_path / "published"
    idmap = tmp_path / "id-map.tsv"

    result = CliRunner().invoke(
        cli,
        ["-a", str(assertions), "--output-dir", str(out), "--id-map-file", str(idmap), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    trigs = list(out.glob("*.trig"))
    # One nanopub, named by its (throwaway, dry-run) artifact code.
    assert len(trigs) == 1
    assert trigs[0].name.startswith("RA")

    parsed = IdMap.from_tsv(idmap.read_text(encoding="utf-8"))
    assert OLD_ID in parsed
    entry = parsed[OLD_ID]
    # The artifact code lands on the thing URI in our namespace.
    assert entry.thing_uri.startswith(f"{NAMESPACE}RA")
    assert entry.np_uri.startswith("https://w3id.org/np/RA")


def test_mint_publish_adds_part_of_type_and_template(tmp_path) -> None:
    DCTERMS = rdflib.Namespace("http://purl.org/dc/terms/")
    NPX = rdflib.Namespace("http://purl.org/nanopub/x/")
    NT = rdflib.Namespace("https://w3id.org/np/o/ntemplate/")
    vocab = "https://w3id.org/spaces/biochementity/r/vocabulary"
    type_uri = "https://w3id.org/peh/terms/BioChemEntity"
    template_uri = "https://w3id.org/np/RAhSlIuuw5YqmMoyyvmy5GL3qIhs7sp14i6x2y3DCOhXM"
    assertions = tmp_path / "assertions"
    assertions.mkdir()
    _assertion_graph(assertions / "caffeine.ttl")
    out = tmp_path / "published"

    result = CliRunner().invoke(
        cli,
        ["-a", str(assertions), "--output-dir", str(out), "--dry-run",
         "--part-of", vocab, "--nanopub-type", type_uri, "--template", template_uri],
    )
    assert result.exit_code == 0, result.output
    np = rdflib.Dataset()
    np.parse(next(out.glob("*.trig")), format="trig")
    triples = {(p, str(o)) for _s, p, o, _g in np.quads((None, None, None, None))}
    # isPartOf in the assertion, the two tags in pubinfo.
    assert (DCTERMS.isPartOf, vocab) in triples
    assert (NPX.hasNanopubType, type_uri) in triples
    assert (NT.wasCreatedFromTemplate, template_uri) in triples


def test_mint_publish_without_keys_requires_dry_run(tmp_path) -> None:
    assertions = tmp_path / "assertions"
    assertions.mkdir()
    _assertion_graph(assertions / "caffeine.ttl")

    result = CliRunner().invoke(
        cli, ["-a", str(assertions), "--output-dir", str(tmp_path / "published")]
    )

    assert result.exit_code != 0
    assert "signing keys" in result.output.lower()


def test_mint_publish_skips_already_minted(tmp_path) -> None:
    assertions = tmp_path / "assertions"
    assertions.mkdir()
    _assertion_graph(assertions / "caffeine.ttl")
    out = tmp_path / "published"
    idmap = tmp_path / "id-map.tsv"
    # Pre-seed the id-map so the term is treated as already minted.
    seed = IdMap.from_tsv("\t".join(("old_id", "thing_uri", "np_uri")) + "\n" + "\t".join(
        (OLD_ID, f"{NAMESPACE}RAseed", "https://w3id.org/np/RAseed")
    ))
    seed.write_tsv(idmap)

    result = CliRunner().invoke(
        cli,
        ["-a", str(assertions), "--output-dir", str(out), "--id-map-file", str(idmap), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    # Nothing newly minted, so no .trig written.
    assert list(out.glob("*.trig")) == []
