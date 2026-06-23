"""Shared signing-material resolution for the minting CLIs.

Turns the common ``--private-key/--use-testsuite-keys/--dry-run`` option set into
a :class:`nanopub.Profile` (or ``None`` for an offline ephemeral key), so the
defining and migration CLIs build their nanopub builders the same way.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import click
import nanopub

from pubmate.rdf2nanopub import NanopubGenerator

logger = logging.getLogger(__name__)


@dataclass
class Signing:
    """Resolved signing material.

    ``profile`` is ``None`` for the offline ephemeral-key case (``--dry-run``
    with no keys); builders treat that as keyless, signing with a throwaway key.
    """

    profile: Optional[nanopub.Profile]
    test_server: bool


def resolve_signing(
    *,
    orcid_id: Optional[str],
    name: Optional[str],
    private_key: Optional[str],
    public_key: Optional[str],
    intro_nanopub_uri: Optional[str],
    use_testsuite_keys: bool,
    testsuite_key: str,
    testsuite_ref: str,
    test_server: bool,
    dry_run: bool,
) -> Signing:
    """Resolve CLI signing options into a :class:`Signing`.

    Precedence: testsuite keys (test server) > explicit key pair > ephemeral
    (only allowed with ``--dry-run``). Raises :class:`click.UsageError` if no
    keys are given and it is not a dry run.
    """
    if use_testsuite_keys:
        generator = NanopubGenerator.from_testsuite_connector(
            key_name=testsuite_key,
            suite_ref=testsuite_ref,
            intro_nanopub_uri=intro_nanopub_uri,
            test_server=True,
        )
        return Signing(profile=generator.profile, test_server=True)

    if private_key and public_key:
        profile = nanopub.Profile(
            orcid_id=orcid_id,
            name=name,
            private_key=private_key,
            public_key=public_key,
            introduction_nanopub_uri=intro_nanopub_uri,
        )
        return Signing(profile=profile, test_server=test_server)

    if not dry_run:
        raise click.UsageError(
            "No signing keys provided. Pass --private-key/--public-key or --use-testsuite-keys, "
            "or use --dry-run for an offline preview with throwaway (ephemeral) keys."
        )
    logger.warning("No keys given; signing with an ephemeral key (--dry-run). Artifact codes are throwaway.")
    return Signing(profile=None, test_server=test_server)
