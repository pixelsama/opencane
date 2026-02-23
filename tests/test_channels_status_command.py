from typer.testing import CliRunner

from opencane.cli.commands import app
from opencane.config.schema import Config

runner = CliRunner()


def test_channels_status_shows_dingtalk_qq_email(monkeypatch) -> None:
    cfg = Config()
    cfg.channels.dingtalk.enabled = True
    cfg.channels.dingtalk.client_id = "dt-client-id"
    cfg.channels.qq.enabled = True
    cfg.channels.qq.app_id = "qq-app-id"
    cfg.channels.email.enabled = True
    cfg.channels.email.imap_host = "imap.example.com"

    monkeypatch.setattr("opencane.config.loader.load_config", lambda: cfg)

    result = runner.invoke(app, ["channels", "status"])
    assert result.exit_code == 0
    assert "DingTalk" in result.stdout
    assert "QQ" in result.stdout
    assert "Email" in result.stdout
    assert "imap.example.com" in result.stdout

