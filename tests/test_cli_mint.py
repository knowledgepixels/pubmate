import re

import yaml
from click.testing import CliRunner

from pubmate.cli.mint import cli


def test_mint_cli_dry_run_prints_updated_yaml_without_overwriting_input(tmp_path) -> None:
    data_path = tmp_path / "terms.yaml"
    original = "vocabulary_terms:\n" "  - name: Alpha\n" "  - name: Beta\n"
    data_path.write_text(original, encoding="utf-8")

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(
        cli,
        [
            "--data",
            str(data_path),
            "--target",
            "vocabulary_terms",
            "--namespace",
            "https://example.org/terms/",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert data_path.read_text(encoding="utf-8") == original

    rendered = yaml.safe_load(result.stdout)
    assert rendered is not None
    assert "vocabulary_terms" in rendered

    ids = [entry["id"] for entry in rendered["vocabulary_terms"]]
    assert len(ids) == 2
    assert all(re.match(r"^https://example.org/terms/[0-9A-HJKMNP-TV-Z]{26}$", identifier) for identifier in ids)


def test_mint_cli_keeps_valid_uri_that_does_not_match_selected_method(tmp_path) -> None:
    data_path = tmp_path / "terms.yaml"
    existing_id = "https://example.org/terms/alpha"
    data_path.write_text(
        ("vocabulary_terms:\n" "  - name: Alpha\n" f"    id: {existing_id}\n"),
        encoding="utf-8",
    )

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(
        cli,
        [
            "--data",
            str(data_path),
            "--target",
            "vocabulary_terms",
            "--namespace",
            "https://example.org/terms/",
            "--method",
            "ulid",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output

    rendered = yaml.safe_load(result.stdout)
    assert rendered["vocabulary_terms"][0]["id"] == existing_id
