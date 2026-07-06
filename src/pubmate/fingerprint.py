"""Semantic drift fingerprint for defining nanopublications.

A defining nanopub's trusty artifact code is a hash of the *whole* nanopub, so it
cannot answer "does the already-published version need re-issuing?": it also moves
with the build timestamp, the signing key and the toolchain, and it only exists
*after* signing. This module computes a stable, key-independent fingerprint over
just the **identity-defining** inputs, so a caller can tell an unchanged term from
one whose content or wrapper has drifted -- and, on drift, supersede rather than
mint a fresh (differently-identified) nanopub.

Three tiers, and where each goes:

* **No-op** -- build timestamp, signature, blank-node ids, triple order,
  serialization prefixes. Excluded: the assertion is URDNA2015-canonicalized
  (:func:`canonical_assertion`), and nothing time/signature-derived enters.
* **Identity-defining** -- the assertion itself (which already carries
  ``dcterms:isPartOf`` when minted with ``part_of``), the suggester and
  ``derived_from`` provenance, and the pubinfo the vocabulary commits to:
  ``dct:license``, ``npx:introduces``, ``npx:hasNanopubType``,
  ``nt:wasCreatedFromTemplate``. This is the fingerprint domain.
* **Build-provenance** -- signing key, ``nanopub``/pubmate version, trusty
  algorithm. Deliberately **not** hashed here (a key rotation must not read as
  content drift); record it beside the fingerprint and gate re-issue on policy.

The fingerprint is computed on the *placeholder* form of the assertion (subject =
``namespace + ~~~ARTIFACTCODE~~~``), i.e. the graph the builder holds before
signing, so the thing URI -- which contains the code, itself a hash of the whole
nanopub -- never feeds its own fingerprint.

Keep :func:`identity_fields` in lockstep with
:meth:`~pubmate.defining.DefiningNanopubBuilder.build`: if a future change adds an
identity-bearing pubinfo triple there, mirror it here and bump :data:`FP_SCHEME`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

import rdflib
from rdflib.compare import to_canonical_graph

from pubmate._nanopub_build import UNSET as _UNSET
from pubmate.defining import DefiningNanopubBuilder
from pubmate.minting import TermInput

#: Fingerprint scheme tag, embedded in every digest. Bump when the domain or the
#: canonicalization changes, so digests from an older scheme are recomputed
#: rather than silently compared across incompatible definitions.
FP_SCHEME = "pubmate-fp-1"


def canonical_assertion(graph: rdflib.Graph) -> str:
    """Canonicalize ``graph`` (URDNA2015) to sorted N-Triples.

    Stable across blank-node labels, triple order and serialization prefixes --
    the no-op tier -- so cosmetic RDF churn does not move the fingerprint.
    """
    canonical = to_canonical_graph(graph)
    lines = canonical.serialize(format="nt").splitlines()
    return "\n".join(sorted(line for line in lines if line.strip()))


@dataclass(frozen=True)
class IdentityFields:
    """The identity-defining inputs a defining nanopub commits to.

    A change to any field means the published nanopub is materially stale and
    should be re-issued by *superseding*. The signing key and toolchain are
    intentionally absent (see the module docstring)."""

    assertion: str
    suggester: str
    derived_from: str
    license: str
    introduces: str
    nanopub_types: tuple[str, ...]
    template: str

    def digest(self) -> str:
        """Hex SHA-256 over the canonical JSON of these fields plus the scheme."""
        payload = {
            "scheme": FP_SCHEME,
            "assertion": self.assertion,
            "suggester": self.suggester,
            "derived_from": self.derived_from,
            "license": self.license,
            "introduces": self.introduces,
            "nanopub_types": list(self.nanopub_types),
            "template": self.template,
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def identity_fields(
    term: TermInput,
    builder: DefiningNanopubBuilder,
    *,
    default_suggester: Optional[str] = None,
    license: Any = _UNSET,
    introduces: Any = _UNSET,
) -> IdentityFields:
    """Extract ``term``'s identity-defining fields as ``builder`` would emit them.

    Mirrors :meth:`DefiningNanopubBuilder.build` and
    :meth:`SequentialMinter.mint` defaulting so the fingerprint tracks exactly
    what gets signed: ``license`` falls back to the builder's license,
    ``introduces`` to the placeholder thing URI, and the suggester to
    ``default_suggester`` when the term carries none. ``part_of`` is already in
    ``term.assertion`` (added by ``term_input_from_assertion``), so it is covered
    by the assertion hash rather than a field here.
    """
    effective_license = builder.license if license is _UNSET else license
    effective_introduces = builder.thing_uri if introduces is _UNSET else introduces
    return IdentityFields(
        assertion=canonical_assertion(term.assertion),
        suggester=term.suggester_orcid or default_suggester or "",
        derived_from=term.derived_from or "",
        license=effective_license or "",
        introduces="" if effective_introduces is None else str(effective_introduces),
        nanopub_types=tuple(builder.nanopub_types),
        template=builder.template or "",
    )


def fingerprint_term(
    term: TermInput,
    builder: DefiningNanopubBuilder,
    *,
    default_suggester: Optional[str] = None,
    license: Any = _UNSET,
    introduces: Any = _UNSET,
) -> str:
    """Hex SHA-256 drift fingerprint for ``term`` as built by ``builder``.

    Convenience wrapper over :func:`identity_fields`; the same defaulting rules
    apply. Two terms share a fingerprint iff they would sign to nanopubs that are
    identical in every identity-defining respect (ignoring timestamp, signature,
    key and blank-node/serialization noise)."""
    return identity_fields(
        term,
        builder,
        default_suggester=default_suggester,
        license=license,
        introduces=introduces,
    ).digest()
