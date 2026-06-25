"""Sequential mint-and-publish orchestrator.

Mint a set of *defining* nanopublications: build each (keeping its assertion to
the term's intrinsic properties), sign it — which computes the trusty artifact
code and therefore the term's final URI — and optionally publish it. The result
exposes two maps the rest of a pipeline needs: ``term_id -> thing URI`` and
``term_id -> nanopub URI``.

Because each defining nanopub is self-contained (no references to other terms in
the batch), minting order does not matter here. Links between terms — including
forward references and cycles — are added afterwards by superseding, once every
referenced term has a stable URI.

The minted artifact code depends on the signing key, so the maps produced with a
test key differ from those produced with the real key; the authoritative map is
the one minted with the real signing key.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import nanopub
import rdflib

from pubmate._nanopub_build import preferred_label
from pubmate.defining import DefiningNanopubBuilder
from pubmate.rdf2nanopub import sign_and_publish
from pubmate.utils import serialize_nanopub

logger = logging.getLogger(__name__)

# Trusty artifact code, e.g. "RA" followed by a base64url-ish hash.
_ARTIFACT_CODE_RE = re.compile(r"RA[A-Za-z0-9_\-]{40,}")

_PROV = rdflib.Namespace("http://www.w3.org/ns/prov#")
_RDFS = rdflib.Namespace("http://www.w3.org/2000/01/rdf-schema#")
_DCTERMS = rdflib.Namespace("http://purl.org/dc/terms/")


def term_input_from_assertion(
    assertion: rdflib.Graph,
    *,
    namespace: str,
    thing_uri: rdflib.URIRef,
    term_id: Optional[str] = None,
    default_suggester: Optional[str] = None,
    part_of: Optional[str] = None,
) -> TermInput:
    """Re-key a per-term assertion onto the placeholder thing URI.

    Assertions emitted by the build pipeline are keyed on the term's *current*
    URI (e.g. a previously minted id). To mint a defining nanopub whose artifact
    code lands on the thing URI, the assertion's subject must be the builder's
    placeholder ``thing_uri``. This finds the single term subject in ``namespace``,
    rewrites it (in subject and object position) to ``thing_uri``, lifts the
    ``prov:wasAttributedTo`` suggester out of the assertion (it belongs in
    provenance, added by the builder), and reads ``rdfs:label`` for the nanopub.

    If ``part_of`` is given, a ``dcterms:isPartOf`` link from the term to that URI
    (e.g. the vocabulary the term belongs to) is added to the assertion.

    The original term URI becomes the returned ``TermInput.term_id`` (the id-map
    key) unless ``term_id`` is given.
    """
    subjects = {
        s for s in assertion.subjects() if isinstance(s, rdflib.URIRef) and str(s).startswith(namespace)
    }
    if len(subjects) != 1:
        raise ValueError(
            f"expected exactly one subject in namespace {namespace!r}, found {len(subjects)}: {sorted(map(str, subjects))}"
        )
    old_subject = next(iter(subjects))

    suggester = next((str(o) for o in assertion.objects(old_subject, _PROV.wasAttributedTo)), None)
    label = preferred_label(assertion, old_subject)

    rekeyed = rdflib.Graph()
    for s, p, o in assertion:
        if s == old_subject and p == _PROV.wasAttributedTo:
            continue  # lifted into provenance by the builder
        new_s = thing_uri if s == old_subject else s
        new_o = thing_uri if o == old_subject else o
        rekeyed.add((new_s, p, new_o))

    if part_of:
        rekeyed.add((thing_uri, _DCTERMS.isPartOf, rdflib.URIRef(part_of)))

    return TermInput(
        term_id=term_id or str(old_subject),
        assertion=rekeyed,
        suggester_orcid=suggester or default_suggester,
        label=label,
    )


@dataclass
class TermInput:
    """One term to mint.

    Attributes:
        term_id: the caller's stable handle for the term (used as the map key
            and, optionally, the output filename). Not part of the nanopub.
        assertion: the term's intrinsic-property graph. Its subject must be the
            builder's :attr:`~pubmate.defining.DefiningNanopubBuilder.thing_uri`
            (the artifact-code placeholder URI) so the code lands on the term.
        suggester_orcid: ORCID the assertion is attributed to; overrides the
            minter's ``default_suggester_orcid`` when set.
        label: optional ``rdfs:label`` for the nanopub.
        derived_from: optional ``prov:wasDerivedFrom`` source.
    """

    term_id: str
    assertion: rdflib.Graph
    suggester_orcid: Optional[str] = None
    label: Optional[str] = None
    derived_from: Optional[str] = None


@dataclass
class MintedTerm:
    """The outcome of minting one term."""

    term_id: str
    thing_uri: str
    np_uri: str
    nanopub: nanopub.Nanopub


@dataclass
class MintBatch:
    """The outcome of minting a batch of terms."""

    terms: List[MintedTerm] = field(default_factory=list)

    @property
    def thing_uri_map(self) -> Dict[str, str]:
        """``term_id -> thing URI`` (the minted term URI in the namespace)."""
        return {t.term_id: t.thing_uri for t in self.terms}

    @property
    def np_uri_map(self) -> Dict[str, str]:
        """``term_id -> nanopub URI``."""
        return {t.term_id: t.np_uri for t in self.terms}


def _artifact_code(np_uri: str) -> str:
    match = _ARTIFACT_CODE_RE.search(np_uri)
    if not match:
        raise ValueError(f"could not extract a trusty artifact code from nanopub URI: {np_uri}")
    return match.group(0)


class SequentialMinter:
    """Mint defining nanopubs one by one with a configured builder.

    Args:
        builder: a :class:`~pubmate.defining.DefiningNanopubBuilder` configured
            with the target namespace and a signing profile.
        default_suggester_orcid: ORCID used to attribute any term whose own
            ``suggester_orcid`` is not set. Intended as a per-batch fallback
            (e.g. during a migration where terms carry no suggester yet); a
            per-term value always takes precedence.
    """

    def __init__(
        self,
        builder: DefiningNanopubBuilder,
        *,
        default_suggester_orcid: Optional[str] = None,
    ):
        self.builder = builder
        self.default_suggester_orcid = default_suggester_orcid

    def mint(self, term: TermInput, *, dry_run: bool = True) -> MintedTerm:
        """Build, sign and (unless ``dry_run``) publish a single term."""
        suggester = term.suggester_orcid or self.default_suggester_orcid
        np = self.builder.build(
            term.assertion,
            suggester_orcid=suggester,
            label=term.label,
            derived_from=term.derived_from,
        )
        np_uri = sign_and_publish(np, dry_run=dry_run)
        thing_uri = f"{self.builder.namespace}{_artifact_code(np_uri)}"
        logger.info("Minted %s -> %s (%s)", term.term_id, thing_uri, np_uri)
        return MintedTerm(term_id=term.term_id, thing_uri=thing_uri, np_uri=np_uri, nanopub=np)

    def mint_all(
        self,
        terms: Iterable[TermInput],
        *,
        dry_run: bool = True,
        already_minted: Optional[Dict[str, str]] = None,
        output_dir: Optional[Path] = None,
    ) -> MintBatch:
        """Mint a batch of terms, skipping any already minted.

        Args:
            terms: the terms to mint.
            dry_run: if True, sign only (offline) without publishing.
            already_minted: ``term_id -> nanopub URI`` for terms minted in a
                previous run; these are skipped so re-runs do not duplicate them.
            output_dir: if given, each minted nanopub is written there as
                ``<term_id>.trig`` in canonical graph order.

        Returns only the newly minted terms.
        """
        skip = set(already_minted or {})
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)

        batch = MintBatch()
        for term in terms:
            if term.term_id in skip:
                logger.info("Skipping already-minted term: %s", term.term_id)
                continue
            minted = self.mint(term, dry_run=dry_run)
            if output_dir is not None:
                (output_dir / f"{term.term_id}.trig").write_text(
                    serialize_nanopub(minted.nanopub), encoding="utf-8"
                )
            batch.terms.append(minted)
        return batch
