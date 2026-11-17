import io
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services import owid
from app.services.owid import BenchmarkDataUnavailableError


def _csv_content():
    return pd.DataFrame(
        [
            {"country": "India", "year": 2022, "iso_code": "IND", "population": 1_400_000_000, "co2": 2_500, "co2_per_capita": 1.8, "co2_per_gdp": 0.3},
            {"country": "India", "year": 2023, "iso_code": "IND", "population": 1_410_000_000, "co2": 2_600, "co2_per_capita": 1.9, "co2_per_gdp": 0.31},
            {"country": "World", "year": 2023, "iso_code": "OWID_WRL", "population": 8_000_000_000, "co2": 36_000, "co2_per_capita": 4.5, "co2_per_gdp": 0.4},
        ]
    ).to_csv(index=False).encode()


def test_owid_refresh_and_benchmark_views_store_attribution(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        owid,
        "get_settings",
        lambda: SimpleNamespace(
            owid_data_url="https://example.org/verified-test-dataset.csv",
            owid_data_directory=str(tmp_path),
        ),
    )
    monkeypatch.setattr(owid, "urlopen", lambda request, timeout: io.BytesIO(_csv_content()))

    registration = client.post(
        "/api/v1/auth/register",
        json={
            "email": "benchmark@example.org",
            "password": "CarbonTracePass123",
            "display_name": "Benchmark User",
            "organization_name": "Benchmark Org",
        },
    )
    organization_id = registration.json()["organization"]["id"]
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}

    refresh = client.post(
        "/api/v1/benchmarking/refresh",
        headers=headers,
        params={"organization_id": organization_id},
    )
    assert refresh.status_code == 201
    dataset_hash = refresh.json()["dataset_sha256"]

    country_list = client.get(
        "/api/v1/benchmarking/countries",
        headers=headers,
        params={"organization_id": organization_id},
    )
    assert country_list.status_code == 200
    assert country_list.json()["dataset_sha256"] == dataset_hash
    assert country_list.json()["items"] == [{"country": "India", "iso_code": "IND"}]

    trends = client.get(
        "/api/v1/benchmarking/IND/trends",
        headers=headers,
        params={"organization_id": organization_id, "year_start": 2023},
    )
    assert trends.status_code == 200
    assert trends.json()["series"][0]["year"] == 2023
    assert trends.json()["organizational_emission_factor"] is False


def test_owid_rejects_non_https_or_unexpected_schema(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        owid,
        "get_settings",
        lambda: SimpleNamespace(owid_data_url="http://example.org/data.csv", owid_data_directory=str(tmp_path)),
    )
    with pytest.raises(ValueError, match="HTTPS"):
        owid.fetch_owid_dataset(None)

    monkeypatch.setattr(
        owid,
        "get_settings",
        lambda: SimpleNamespace(owid_data_url="https://example.org/data.csv", owid_data_directory=str(tmp_path)),
    )
    monkeypatch.setattr(owid, "urlopen", lambda request, timeout: io.BytesIO(b"country,year\nIndia,2023\n"))
    with pytest.raises(BenchmarkDataUnavailableError, match="schema"):
        owid.fetch_owid_dataset(None)
