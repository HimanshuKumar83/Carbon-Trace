import hashlib
import io
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import BenchmarkDataset

OWID_REQUIRED_COLUMNS = ("country", "year", "iso_code", "population", "co2", "co2_per_capita", "co2_per_gdp")
OWID_REPOSITORY = "https://github.com/owid/co2-data"
OWID_CODEBOOK_SHA = "437d6ee69383ede35d6fc939914eae13311f0583"
MAX_DATASET_BYTES = 100 * 1024 * 1024


class BenchmarkDataUnavailableError(RuntimeError):
    pass


def fetch_owid_dataset(db: Session) -> BenchmarkDataset:
    settings = get_settings()
    parsed_url = urlparse(settings.owid_data_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise ValueError("OWID_DATA_URL must be an HTTPS URL.")
    request = Request(settings.owid_data_url, headers={"User-Agent": "CarbonTrace/0.1 (OWID dataset refresh)"})
    try:
        with urlopen(request, timeout=60) as response:
            content = response.read(MAX_DATASET_BYTES + 1)
    except URLError as error:
        raise BenchmarkDataUnavailableError("The configured OWID dataset could not be downloaded.") from error
    if len(content) > MAX_DATASET_BYTES:
        raise BenchmarkDataUnavailableError("The downloaded OWID dataset exceeds the configured size ceiling.")

    try:
        header = pd.read_csv(io.BytesIO(content), nrows=0)
    except (pd.errors.ParserError, UnicodeDecodeError) as error:
        raise BenchmarkDataUnavailableError("The OWID response was not a readable CSV dataset.") from error
    missing_columns = sorted(set(OWID_REQUIRED_COLUMNS) - set(header.columns))
    if missing_columns:
        raise BenchmarkDataUnavailableError(f"OWID dataset schema is missing required columns: {', '.join(missing_columns)}.")
    try:
        frame = pd.read_csv(io.BytesIO(content), usecols=list(OWID_REQUIRED_COLUMNS), low_memory=False)
    except (pd.errors.ParserError, ValueError, UnicodeDecodeError) as error:
        raise BenchmarkDataUnavailableError("The OWID CSV could not be parsed using the verified schema.") from error
    frame = frame.dropna(subset=["country", "year", "iso_code"])
    frame["year"] = pd.to_numeric(frame["year"], errors="coerce")
    frame = frame.dropna(subset=["year"])
    if frame.empty:
        raise BenchmarkDataUnavailableError("The OWID dataset contains no usable country/year records.")

    dataset_sha256 = hashlib.sha256(content).hexdigest()
    settings_directory = Path(settings.owid_data_directory).resolve()
    settings_directory.mkdir(parents=True, exist_ok=True)
    storage_path = settings_directory / f"owid-co2-data-{dataset_sha256[:16]}.csv"
    if not storage_path.exists():
        storage_path.write_bytes(content)
    retrieved_at = datetime.now(timezone.utc)
    dataset = BenchmarkDataset(
        source_url=settings.owid_data_url,
        dataset_sha256=dataset_sha256,
        storage_key=str(storage_path),
        row_count=len(frame),
        columns=list(OWID_REQUIRED_COLUMNS),
        retrieved_at=retrieved_at,
    )
    existing = db.scalar(
        select(BenchmarkDataset).where(BenchmarkDataset.dataset_sha256 == dataset_sha256)
    )
    if existing is not None:
        existing.retrieved_at = retrieved_at
        existing.source_url = settings.owid_data_url
        existing.storage_key = str(storage_path)
        existing.row_count = len(frame)
        existing.columns = list(OWID_REQUIRED_COLUMNS)
        db.commit()
        db.refresh(existing)
        return existing
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    load_benchmark_frame.cache_clear()
    return dataset


def get_latest_dataset(db: Session) -> BenchmarkDataset:
    dataset = db.scalar(select(BenchmarkDataset).order_by(BenchmarkDataset.retrieved_at.desc()).limit(1))
    if dataset is None or not Path(dataset.storage_key).is_file():
        raise BenchmarkDataUnavailableError("No cached OWID dataset is available; refresh the benchmark dataset first.")
    return dataset


@lru_cache(maxsize=3)
def load_benchmark_frame(storage_key: str, dataset_sha256: str) -> pd.DataFrame:
    del dataset_sha256
    try:
        frame = pd.read_csv(storage_key, usecols=list(OWID_REQUIRED_COLUMNS), low_memory=False)
    except (OSError, pd.errors.ParserError, ValueError, UnicodeDecodeError) as error:
        raise BenchmarkDataUnavailableError("Cached OWID benchmark data could not be read.") from error
    frame["year"] = pd.to_numeric(frame["year"], errors="coerce")
    for column in ("population", "co2", "co2_per_capita", "co2_per_gdp"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def list_benchmark_countries(db: Session) -> tuple[BenchmarkDataset, list[dict[str, str]]]:
    dataset = get_latest_dataset(db)
    frame = load_benchmark_frame(dataset.storage_key, dataset.dataset_sha256)
    locations = frame[["country", "iso_code"]].dropna().drop_duplicates()
    locations = locations[locations["iso_code"].astype(str).str.fullmatch(r"[A-Z]{3}")]
    countries = [
        {"country": str(row.country), "iso_code": str(row.iso_code)}
        for row in locations.sort_values("country").itertuples(index=False)
    ]
    return dataset, countries


def get_country_trends(
    db: Session,
    country: str,
    year_start: int | None = None,
    year_end: int | None = None,
) -> dict:
    dataset = get_latest_dataset(db)
    frame = load_benchmark_frame(dataset.storage_key, dataset.dataset_sha256)
    lookup = country.strip()
    country_rows = frame[
        (frame["iso_code"].astype(str).str.upper() == lookup.upper())
        | (frame["country"].astype(str).str.casefold() == lookup.casefold())
    ]
    if country_rows.empty:
        raise KeyError("Country or ISO alpha-3 code is not available in the cached OWID dataset.")
    country_name = str(country_rows.iloc[0]["country"])
    country_iso = str(country_rows.iloc[0]["iso_code"])
    if year_start is not None:
        country_rows = country_rows[country_rows["year"] >= year_start]
    if year_end is not None:
        country_rows = country_rows[country_rows["year"] <= year_end]
    country_rows = country_rows.sort_values("year")

    items = []
    for row in country_rows.itertuples(index=False):
        items.append(
            {
                "year": int(row.year),
                "co2_million_tonnes": None if pd.isna(row.co2) else float(row.co2),
                "co2_per_capita_tonnes_per_person": None if pd.isna(row.co2_per_capita) else float(row.co2_per_capita),
                "population": None if pd.isna(row.population) else int(row.population),
                "co2_per_gdp_kg_per_international_dollar": None if pd.isna(row.co2_per_gdp) else float(row.co2_per_gdp),
            }
        )
    return {
        "country": country_name,
        "iso_code": country_iso,
        "source": "Our World in Data CO2 and Greenhouse Gas Emissions dataset",
        "source_repository": OWID_REPOSITORY,
        "dataset_sha256": dataset.dataset_sha256,
        "retrieved_at": dataset.retrieved_at.isoformat(),
        "units": {
            "co2_million_tonnes": "million tonnes",
            "co2_per_capita_tonnes_per_person": "tonnes per person",
            "population": "people",
            "co2_per_gdp_kg_per_international_dollar": "kg per 2011 international-$",
        },
        "series": items,
        "organizational_emission_factor": False,
    }
