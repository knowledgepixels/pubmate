import os


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--pubmate-run-testserver-publish",
        action="store",
        default=None,
        help="Value for PUBMATE_RUN_TESTSERVER_PUBLISH (default: 1).",
    )
    parser.addoption(
        "--pubmate-testsuite-key",
        action="store",
        default=None,
        help="Value for PUBMATE_TESTSUITE_KEY (default: rsa-key1).",
    )
    parser.addoption(
        "--pubmate-testsuite-ref",
        action="store",
        default=None,
        help="Value for PUBMATE_TESTSUITE_REF (default: main).",
    )
    parser.addoption(
        "--pubmate-run-nanopub-existence-check",
        action="store",
        default=None,
        help="Value for PUBMATE_RUN_NANOPUB_EXISTENCE_CHECK (default: 1).",
    )


def pytest_configure(config) -> None:
    run_testserver_publish = config.getoption("--pubmate-run-testserver-publish")
    testsuite_key = config.getoption("--pubmate-testsuite-key")
    testsuite_ref = config.getoption("--pubmate-testsuite-ref")
    run_nanopub_existence_check = config.getoption("--pubmate-run-nanopub-existence-check")

    os.environ["PUBMATE_RUN_TESTSERVER_PUBLISH"] = run_testserver_publish or os.getenv(
        "PUBMATE_RUN_TESTSERVER_PUBLISH", "1"
    )
    os.environ["PUBMATE_TESTSUITE_KEY"] = testsuite_key or os.getenv("PUBMATE_TESTSUITE_KEY", "rsa-key1")
    os.environ["PUBMATE_TESTSUITE_REF"] = testsuite_ref or os.getenv("PUBMATE_TESTSUITE_REF", "main")
    os.environ["PUBMATE_RUN_NANOPUB_EXISTENCE_CHECK"] = run_nanopub_existence_check or os.getenv(
        "PUBMATE_RUN_NANOPUB_EXISTENCE_CHECK", "1"
    )
