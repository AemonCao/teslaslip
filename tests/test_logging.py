import logging

from app.redact import RedactFilter, SecretVault, redact_text


def test_log_sample_redacts_coordinates_token_device_key_and_password():
    sample = (
        "latitude=31.234567 longitude: 121.4737 "
        "GET /d/abcdefghijklmnop "
        "device_key=bark-device-secret password=mqtt-secret-value "
        "postgresql://teslaslip_ro:db-secret-value@192.168.1.223:45432/teslamate"
    )
    secrets = [
        "bark-device-secret",
        "mqtt-secret-value",
        "db-secret-value",
        "postgresql://teslaslip_ro:db-secret-value@192.168.1.223:45432/teslamate",
    ]
    redacted = redact_text(sample, secrets, redact_tokens=True)
    assert "31.234567" not in redacted
    assert "121.4737" not in redacted
    assert "bark-device-secret" not in redacted
    assert "mqtt-secret-value" not in redacted
    assert "db-secret-value" not in redacted
    assert "/d/[redacted]" in redacted

    vault = SecretVault(secrets)
    logger = logging.getLogger("teslaslip.testsample")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    found: list[str] = []

    class ListHandler(logging.Handler):
        def emit(self, record):
            found.append(record.getMessage())

    handler = ListHandler()
    handler.addFilter(RedactFilter(vault, redact_tokens=True))
    logger.addHandler(handler)
    logger.info(sample)
    assert found
    assert "31.234567" not in found[0]
    assert "bark-device-secret" not in found[0]
    assert "db-secret-value" not in found[0]
