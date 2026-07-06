import rdflib

from pubmate.defining import DefiningNanopubBuilder
from pubmate.fingerprint import (
    canonical_assertion,
    fingerprint_term,
    identity_fields,
)
from pubmate.minting import TermInput, term_input_from_assertion

NAMESPACE = "https://w3id.org/peh/biochementities/"
TEMPLATE = "https://w3id.org/np/RAtemplate"
NANOPUB_TYPE = "https://w3id.org/peh/terms/BioChemEntity"
SUGGESTER = "https://orcid.org/0000-0002-1825-0097"

# A term assertion with a blank node (a context-alias) so canonicalization has
# something non-trivial to normalize.
TERM_TTL = """
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix schema1: <http://schema.org/> .
@prefix pehterms: <https://w3id.org/peh/terms/> .
<https://w3id.org/peh/biochementities/00fb5fbc05> a <http://www.w3.org/2002/07/owl#Class> ;
    rdfs:label "Polychlorinated biphenyl 187" ;
    schema1:alternateName "PCB 187" ;
    pehterms:hasContextAlias [ a pehterms:ContextAlias ;
            schema1:alternateName "pcb187" ;
            schema1:identifier "short_name" ] .
"""


def _builder(**kwargs) -> DefiningNanopubBuilder:
    kwargs.setdefault("nanopub_types", [NANOPUB_TYPE])
    kwargs.setdefault("template", TEMPLATE)
    return DefiningNanopubBuilder(NAMESPACE, **kwargs)


def _term(ttl: str = TERM_TTL, *, part_of=None, default_suggester=None) -> TermInput:
    graph = rdflib.Graph()
    graph.parse(data=ttl, format="turtle")
    builder = _builder()
    return term_input_from_assertion(
        graph,
        namespace=NAMESPACE,
        thing_uri=builder.thing_uri,
        part_of=part_of,
        default_suggester=default_suggester,
    )


def test_fingerprint_is_deterministic():
    builder = _builder()
    term = _term()
    assert fingerprint_term(term, builder) == fingerprint_term(term, builder)


def test_digest_is_hex_sha256():
    digest = fingerprint_term(_term(), _builder())
    assert len(digest) == 64
    int(digest, 16)  # raises if not hex


def test_scheme_participates_in_digest(monkeypatch):
    # The scheme tag is inside the hashed payload: changing it moves the digest,
    # so digests are never silently compared across incompatible schemes.
    fields = identity_fields(_term(), _builder())
    before = fields.digest()
    monkeypatch.setattr("pubmate.fingerprint.FP_SCHEME", "pubmate-fp-test")
    assert fields.digest() != before


def test_cosmetic_reserialization_does_not_change_fingerprint():
    # Round-trip through N-Triples (relabels blank nodes, reorders triples): a
    # no-op in the identity domain.
    builder = _builder()
    base = _term()
    churned = rdflib.Graph()
    churned.parse(data=base.assertion.serialize(format="nt"), format="nt")
    reserialized = TermInput(
        term_id=base.term_id,
        assertion=churned,
        suggester_orcid=base.suggester_orcid,
        label=base.label,
    )
    assert canonical_assertion(base.assertion) == canonical_assertion(churned)
    assert fingerprint_term(base, builder) == fingerprint_term(reserialized, builder)


def test_assertion_change_moves_fingerprint():
    builder = _builder()
    edited = TERM_TTL.replace("Polychlorinated biphenyl 187", "Polychlorinated biphenyl 187 (edited)")
    assert fingerprint_term(_term(), builder) != fingerprint_term(_term(edited), builder)


def test_part_of_change_moves_fingerprint():
    builder = _builder()
    a = fingerprint_term(_term(part_of="https://w3id.org/spaces/biochementity/r/vocabulary"), builder)
    b = fingerprint_term(_term(part_of="https://w3id.org/spaces/biochementity/r/vocabulary-v2"), builder)
    assert a != b


def test_template_change_moves_fingerprint():
    term = _term()
    a = fingerprint_term(term, _builder(template=TEMPLATE))
    b = fingerprint_term(term, _builder(template="https://w3id.org/np/RAother"))
    assert a != b


def test_nanopub_type_change_moves_fingerprint():
    term = _term()
    a = fingerprint_term(term, _builder(nanopub_types=[NANOPUB_TYPE]))
    b = fingerprint_term(term, _builder(nanopub_types=[NANOPUB_TYPE, "https://w3id.org/peh/terms/Extra"]))
    assert a != b


def test_license_change_moves_fingerprint():
    term = _term()
    a = fingerprint_term(term, _builder(license="https://creativecommons.org/licenses/by/4.0/"))
    b = fingerprint_term(term, _builder(license="https://creativecommons.org/publicdomain/zero/1.0/"))
    assert a != b


def test_suggester_change_moves_fingerprint():
    builder = _builder()
    a = fingerprint_term(_term(default_suggester=SUGGESTER), builder)
    b = fingerprint_term(_term(default_suggester="https://orcid.org/0000-0001-0000-0000"), builder)
    assert a != b


def test_default_suggester_fallback_matches_resolved_term_suggester():
    # A term with no suggester + a batch default must fingerprint the same as a
    # term that already carries that suggester.
    builder = _builder()
    via_default = fingerprint_term(_term(), builder, default_suggester=SUGGESTER)
    via_term = fingerprint_term(_term(default_suggester=SUGGESTER), builder)
    assert via_default == via_term


def test_namespace_change_moves_fingerprint():
    # The namespace rides in via the placeholder subject / introduces URI.
    term = _term()
    a = fingerprint_term(term, DefiningNanopubBuilder(NAMESPACE))
    b = fingerprint_term(term, DefiningNanopubBuilder("https://example.org/other/"))
    assert a != b
