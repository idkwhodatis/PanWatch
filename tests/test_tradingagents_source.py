"""Build-time source selection must not become arbitrary pip/shell input."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/prepare-tradingagents-requirements.py"
spec = importlib.util.spec_from_file_location("ta_requirements", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
FORK = "git+https://github.com/idkwhodatis/TradingAgents.git@c12865ae3d4be7790853a1e047d067a0a9931821"


@pytest.mark.parametrize("source,expected", [("", None), ("0.6.0", "tradingagents==0.6.0"),
    ("1.2.3rc1", "tradingagents==1.2.3rc1"), (FORK, "tradingagents @ " + FORK)])
def test_source_grammar(source, expected):
    assert module.source_requirement(source) == expected


@pytest.mark.parametrize("source", [" ", "0.6.0\nother-package", "0.6.0 --index-url x", "tradingagents==0.6.0",
    "other-package==0.6.0", "-r evil.txt", "0.6.0; python_version > '3'", "$(touch owned)",
    FORK + "#subdirectory=x", FORK + "?secret=TOKEN", FORK.replace("github.com", "TOKEN@github.com"),
    FORK.replace("github.com", "user:TOKEN@github.com"), FORK.replace("git+https", "git+ssh"),
    FORK.replace("c12865ae3d4be7790853a1e047d067a0a9931821", "main"), FORK + "\n", "file:///tmp/repo", FORK.replace("github.com", "github..com"), FORK.replace("github.com", "github.-com")])
def test_bad_source_is_rejected_without_echo(source):
    with pytest.raises(ValueError) as exc:
        module.source_requirement(source)
    if source.strip():
        assert source not in str(exc.value)
    assert "TOKEN" not in str(exc.value)


def test_default_preserves_entire_manifest():
    text = (ROOT / "requirements-runtime.txt").read_text()
    assert module.prepare_manifest(text) == text
    assert "TauricResearch/TradingAgents.git@2d17df8da1536c121e4d7395ac5a5dcec9e96d6f" in text
    assert "idkwhodatis" not in text


def test_only_selected_dependency_changes_and_noneditable_is_explicit():
    text = "# comment\n-r more.txt\n-e ./packages/marketdata\nother==2\ntradingagents @ git+https://example.com/owner/repo.git@v1\n"
    expected = text.replace(text.splitlines()[-1], "tradingagents @ " + FORK)
    assert module.prepare_manifest(text, FORK) == expected
    assert module.prepare_manifest(text, FORK, non_editable=True) == expected.replace("-e ./", "./")


@pytest.mark.parametrize("text", ["other==2\n", "tradingagents==1\ntradingagents==2\n"])
def test_missing_or_duplicate_dependency_rejected(text):
    with pytest.raises(ValueError):
        module.prepare_manifest(text, FORK)


def test_cli_bad_source_does_not_echo_credentials():
    result = subprocess.run([sys.executable, str(SCRIPT), "--source", FORK.replace("github.com", "TOKEN@github.com")], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0
    assert "TOKEN" not in result.stdout + result.stderr


def test_cli_rejects_wrong_working_directory(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode != 0
    assert "repository root" in result.stderr


def test_cli_rejects_replacing_source_manifest():
    result = subprocess.run([sys.executable, str(SCRIPT), "--output", "requirements-runtime.txt"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0


@pytest.mark.parametrize("arguments", [["--source", "--TOKEN=secret"], ["--TOKEN=secret"], ["--source", "TOKEN\nsecret"]])
def test_argument_parser_never_echoes_rejected_input(arguments):
    result = subprocess.run([sys.executable, str(SCRIPT), *arguments], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0
    assert "TOKEN" not in result.stdout + result.stderr


def test_install_replaces_only_selected_distribution_before_normal_resolution(monkeypatch, tmp_path):
    manifest = tmp_path / '.requirements-custom.txt'
    manifest.write_text('other==2\ntradingagents==0.6.0\n-e ./packages/example\n')
    calls = []
    monkeypatch.setattr(module.subprocess, 'run', lambda args, **kwargs: calls.append((args, kwargs)))
    module.install_manifest(manifest)
    assert calls == [
        ([sys.executable, '-m', 'pip', 'install', '--force-reinstall', '--no-deps', 'tradingagents==0.6.0'], {'check': True}),
        ([sys.executable, '-m', 'pip', 'install', '-r', str(manifest)], {'check': True}),
    ]


def test_failed_source_replacement_does_not_continue_with_old_source(monkeypatch, tmp_path):
    manifest = tmp_path / '.requirements-custom.txt'
    manifest.write_text('tradingagents==0.6.0\n')
    calls = []
    def fail(args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(module.subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError):
        module.install_manifest(manifest)
    assert len(calls) == 1



def test_cli_explicit_empty_source_overrides_environment(monkeypatch):
    monkeypatch.setenv('TRADINGAGENTS_SOURCE', FORK)
    output = ROOT / '.requirements-source-test.txt'
    try:
        result = subprocess.run([sys.executable, str(SCRIPT), '--source', '', '--output', output.name], cwd=ROOT, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert output.read_text() == (ROOT / 'requirements-runtime.txt').read_text()
    finally:
        output.unlink(missing_ok=True)


def test_cli_environment_generates_custom_manifest(monkeypatch):
    monkeypatch.setenv('TRADINGAGENTS_SOURCE', FORK)
    output = ROOT / '.requirements-source-test.txt'
    try:
        result = subprocess.run([sys.executable, str(SCRIPT), '--non-editable', '--output', output.name], cwd=ROOT, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert 'tradingagents @ ' + FORK in output.read_text()
        assert '\n-e ' not in output.read_text()
    finally:
        output.unlink(missing_ok=True)


def test_docker_source_argument_stays_in_builder():
    docker = (ROOT / 'Dockerfile').read_text()
    builder = docker.split('AS python-builder', 1)[1].split('# ===== Stage 3:', 1)[0]
    runtime = docker.split('# ===== Stage 3:', 1)[1]
    assert 'ARG TRADINGAGENTS_SOURCE=""' in builder
    assert 'prepare-tradingagents-requirements.py --non-editable' in builder
    assert '-r .requirements-custom.txt' in builder
    assert 'TRADINGAGENTS_SOURCE' not in runtime
