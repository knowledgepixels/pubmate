from click.testing import CliRunner

from pubmate.cli import publish as publish_cli


def _write_minimal_assertion(folder, name: str = "a.ttl") -> None:
    (folder / name).write_text(
        "<https://example.org/s> <https://example.org/p> <https://example.org/o> .\n",
        encoding="utf-8",
    )


def test_publish_cli_uses_testsuite_keys_in_dry_run(tmp_path, monkeypatch) -> None:
    assertion_dir = tmp_path / "assertions"
    assertion_dir.mkdir()
    _write_minimal_assertion(assertion_dir)

    calls: dict = {}

    class DummyGenerator:
        def publish_sequence(self, assertions, dry_run):
            calls["assertion_count"] = len(assertions)
            calls["dry_run"] = dry_run
            return ["https://w3id.org/np/RA-test-1"]

    def fake_from_testsuite_connector(key_name: str, suite_ref: str, test_server: bool):
        calls["key_name"] = key_name
        calls["suite_ref"] = suite_ref
        calls["test_server"] = test_server
        return DummyGenerator()

    monkeypatch.setattr(
        publish_cli.NanopubGenerator,
        "from_testsuite_connector",
        staticmethod(fake_from_testsuite_connector),
    )

    result = CliRunner().invoke(
        publish_cli.cli,
        [
            "--assertion-folder",
            str(assertion_dir),
            "--dry-run",
            "--use-testsuite-keys",
            "--testsuite-key",
            "rsa-key2",
            "--testsuite-ref",
            "main",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == {
        "key_name": "rsa-key2",
        "suite_ref": "main",
        "test_server": True,
        "assertion_count": 1,
        "dry_run": True,
    }


def test_publish_cli_writes_term_to_nanopub_mapping_file(tmp_path, monkeypatch) -> None:
    assertion_dir = tmp_path / "assertions"
    assertion_dir.mkdir()
    _write_minimal_assertion(assertion_dir, "term-b.ttl")
    _write_minimal_assertion(assertion_dir, "term-a.ttl")
    output_file = tmp_path / "redirects" / "mapping.tsv"

    class DummyGenerator:
        def publish_sequence(self, assertions, dry_run):
            assert len(assertions) == 2
            assert dry_run is True
            return [
                "https://w3id.org/np/RA-term-a",
                "https://w3id.org/np/RA-term-b",
            ]

    monkeypatch.setattr(
        publish_cli.NanopubGenerator,
        "from_testsuite_connector",
        staticmethod(lambda key_name, suite_ref, test_server: DummyGenerator()),
    )

    result = CliRunner().invoke(
        publish_cli.cli,
        [
            "--assertion-folder",
            str(assertion_dir),
            "--dry-run",
            "--use-testsuite-keys",
            "--redirect-output-file",
            str(output_file),
        ],
    )

    assert result.exit_code == 0, result.output
    assert output_file.read_text(encoding="utf-8") == (
        "term_id\tnanopub_uri\n" "term-a\thttps://w3id.org/np/RA-term-a\n" "term-b\thttps://w3id.org/np/RA-term-b\n"
    )


def test_publish_cli_rejects_testsuite_keys_without_dry_run(tmp_path) -> None:
    assertion_dir = tmp_path / "assertions"
    assertion_dir.mkdir()
    _write_minimal_assertion(assertion_dir)

    result = CliRunner().invoke(
        publish_cli.cli,
        [
            "--assertion-folder",
            str(assertion_dir),
            "--use-testsuite-keys",
        ],
    )

    assert result.exit_code != 0
    assert "--use-testsuite-keys is only supported with --dry-run." in result.output


def test_publish_cli_requires_manual_credentials_without_testsuite(tmp_path) -> None:
    assertion_dir = tmp_path / "assertions"
    assertion_dir.mkdir()
    _write_minimal_assertion(assertion_dir)

    result = CliRunner().invoke(
        publish_cli.cli,
        [
            "--assertion-folder",
            str(assertion_dir),
            "--dry-run",
        ],
    )

    assert result.exit_code != 0
    assert "Missing required options in manual-key mode:" in result.output


def test_publish_cli_hides_advanced_testsuite_override_flags_in_help() -> None:
    result = CliRunner().invoke(publish_cli.cli, ["--help"])

    assert result.exit_code == 0
    assert "--use-testsuite-keys" in result.output
    assert "--testsuite-key" not in result.output
    assert "--testsuite-ref" not in result.output
