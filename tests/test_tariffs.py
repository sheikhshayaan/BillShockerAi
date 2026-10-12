from config import load_config
from services.tariffs import load_tariffs, tariff_status, verified_version


def test_shipped_tariff_file_is_unverified_placeholder():
    data = load_tariffs(load_config()["TARIFF_FILE"])
    assert data["utility"] == "FESCO"
    assert verified_version(data) is None
    assert tariff_status(data)["verified"] is False


def test_only_complete_verified_versions_are_used():
    data = {"versions": [
        {"id": "a", "status": "verified", "effective_from": "2025-01-01", "categories": {"x": 1}, "source": {"name": "n"}},
        {"id": "b", "status": "verified", "effective_from": "2025-07-01", "categories": {"x": 2}},
        {"id": "c", "status": "unverified", "effective_from": "2026-01-01", "categories": {"x": 3}},
        {"id": "d", "status": "verified", "effective_from": "2026-02-01", "categories": {}},
    ]}
    assert verified_version(data)["id"] == "b"
    assert verified_version(data, on_date="2025-03-01")["id"] == "a"
