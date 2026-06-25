import re

import rdflib
from rdflib import Literal
from rdflib.namespace import RDF, RDFS

from pubmate.defining import DefiningNanopubBuilder
from pubmate.minting import MintBatch, SequentialMinter, TermInput

NAMESPACE = "https://example.org/terms/"
PROV = rdflib.Namespace("http://www.w3.org/ns/prov#")
ALICE = "https://orcid.org/0000-0002-1825-0097"
BOB = "https://orcid.org/0000-0001-2345-6789"
CODE_RE = re.compile(r"^RA[A-Za-z0-9_\-]{40,}$")


def _minter(**kwargs) -> SequentialMinter:
    # Keyless builder: ephemeral in-memory keys, so signing works offline.
    return SequentialMinter(DefiningNanopubBuilder(NAMESPACE), **kwargs)


def _term(term_id: str, label: str, **kwargs) -> TermInput:
    builder = DefiningNanopubBuilder(NAMESPACE)
    assertion = builder.make_assertion([(RDF.type, RDFS.Class), (RDFS.label, Literal(label))])
    return TermInput(term_id=term_id, assertion=assertion, label=label, **kwargs)


def test_mint_assigns_code_to_thing_uri_and_nanopub():
    minter = _minter()
    minted = minter.mint(_term("alpha", "Alpha"), dry_run=True)

    code = minted.np_uri.rstrip("/").split("/")[-1]
    assert CODE_RE.match(code)
    # Same code on the thing URI (our namespace) and the nanopub URI (scheme A).
    assert minted.thing_uri == f"{NAMESPACE}{code}"
    assert "~~~ARTIFACTCODE~~~" not in minted.nanopub.rdf.serialize(format="trig")


def test_per_term_suggester_overrides_default():
    minter = _minter(default_suggester_orcid=ALICE)
    minted = minter.mint(_term("beta", "Beta", suggester_orcid=BOB), dry_run=True)
    assert (None, PROV.wasAttributedTo, rdflib.URIRef(BOB)) in minted.nanopub.provenance
    assert (None, PROV.wasAttributedTo, rdflib.URIRef(ALICE)) not in minted.nanopub.provenance


def test_default_suggester_used_when_term_has_none():
    minter = _minter(default_suggester_orcid=ALICE)
    minted = minter.mint(_term("gamma", "Gamma"), dry_run=True)
    assert (None, PROV.wasAttributedTo, rdflib.URIRef(ALICE)) in minted.nanopub.provenance


def test_no_attribution_when_neither_set():
    minter = _minter()
    minted = minter.mint(_term("delta", "Delta"), dry_run=True)
    assert (None, PROV.wasAttributedTo, None) not in minted.nanopub.provenance


def test_mint_all_builds_maps_and_unique_codes():
    minter = _minter(default_suggester_orcid=ALICE)
    batch = minter.mint_all([_term("a", "A"), _term("b", "B")], dry_run=True)

    assert isinstance(batch, MintBatch)
    assert set(batch.thing_uri_map) == {"a", "b"}
    assert set(batch.np_uri_map) == {"a", "b"}
    # Distinct content -> distinct artifact codes.
    assert batch.np_uri_map["a"] != batch.np_uri_map["b"]


def test_mint_all_skips_already_minted():
    minter = _minter()
    batch = minter.mint_all(
        [_term("a", "A"), _term("b", "B")],
        dry_run=True,
        already_minted={"a": "https://w3id.org/np/RAprevious"},
    )
    assert [t.term_id for t in batch.terms] == ["b"]


def test_mint_all_writes_trig_files(tmp_path):
    minter = _minter()
    minter.mint_all([_term("a", "A"), _term("b", "B")], dry_run=True, output_dir=tmp_path)

    for name in ("a", "b"):
        path = tmp_path / f"{name}.trig"
        assert path.exists()
        text = path.read_text()
        # Canonical serialization: Head graph block first, no leftover placeholder.
        assert text.index("Head {") < text.index("assertion {")
        assert "~~~ARTIFACTCODE~~~" not in text


def test_term_input_prefers_untagged_label_over_localized():
    from pubmate.minting import term_input_from_assertion
    g = rdflib.Graph()
    s = rdflib.URIRef(NAMESPACE + "lead")
    g.add((s, RDF.type, RDFS.Class))
    g.add((s, RDFS.label, Literal("Lood", lang="nl-be")))
    g.add((s, RDFS.label, Literal("Lead")))  # untagged "regular" one
    term = term_input_from_assertion(g, namespace=NAMESPACE, thing_uri=rdflib.URIRef(NAMESPACE + "X"))
    assert term.label == "Lead"


def test_term_input_adds_part_of_link():
    from pubmate.minting import term_input_from_assertion
    DCTERMS = rdflib.Namespace("http://purl.org/dc/terms/")
    thing = rdflib.URIRef(NAMESPACE + "X")
    vocab = "https://w3id.org/spaces/biochementity/r/vocabulary"
    g = rdflib.Graph()
    s = rdflib.URIRef(NAMESPACE + "lead")
    g.add((s, RDF.type, RDFS.Class))
    term = term_input_from_assertion(g, namespace=NAMESPACE, thing_uri=thing, part_of=vocab)
    assert (thing, DCTERMS.isPartOf, rdflib.URIRef(vocab)) in term.assertion
    # No part_of -> no isPartOf triple.
    plain = term_input_from_assertion(g, namespace=NAMESPACE, thing_uri=thing)
    assert (None, DCTERMS.isPartOf, None) not in plain.assertion


def test_preferred_label_falls_back_english_then_any():
    from pubmate._nanopub_build import preferred_label
    s = rdflib.URIRef(NAMESPACE + "x")
    # only localized + english -> english wins over nl-be
    g = rdflib.Graph()
    g.add((s, RDFS.label, Literal("Lood", lang="nl-be")))
    g.add((s, RDFS.label, Literal("Lead", lang="en")))
    assert preferred_label(g, s) == "Lead"
    # only localized -> still returns something (deterministic)
    g2 = rdflib.Graph()
    g2.add((s, RDFS.label, Literal("Lood", lang="nl-be")))
    assert preferred_label(g2, s) == "Lood"
    # no label -> None
    assert preferred_label(rdflib.Graph(), s) is None
