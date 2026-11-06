"""Long-term statistics import for Administrationsservice Fyn."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any, Literal, cast

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import StatisticsMeta
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter, VolumeConverter

from .api import as_float
from .const import (
    DOMAIN,
    SENSOR_ELECTRICITY,
    SENSOR_GAS,
    SENSOR_HEAT,
    SENSOR_HOT_WATER,
    SENSOR_WATER,
)

try:  # Home Assistant 2026.3 and newer
    from homeassistant.components.recorder.models import StatisticMeanType
except ImportError:  # Home Assistant 2024.11 - 2026.2
    StatisticMeanType = None  # type: ignore[assignment]

_LOGGER = logging.getLogger(__name__)

# Extra metadata keys are only supported by newer Home Assistant releases.
# Passing them to older releases breaks StatisticsMeta.from_meta().
_META_KEYS = set(StatisticsMeta.__table__.columns.keys())

SENSOR_LABELS = {
    SENSOR_ELECTRICITY: "Electricity",
    SENSOR_HEAT: "District heating",
    SENSOR_HOT_WATER: "Hot water",
    SENSOR_WATER: "Water",
    SENSOR_GAS: "Gas",
}

UNIT_CLASSES = {
    SENSOR_ELECTRICITY: EnergyConverter.UNIT_CLASS,
    SENSOR_HEAT: EnergyConverter.UNIT_CLASS,
    SENSOR_HOT_WATER: VolumeConverter.UNIT_CLASS,
    SENSOR_WATER: VolumeConverter.UNIT_CLASS,
    SENSOR_GAS: VolumeConverter.UNIT_CLASS,
}


def _id_part(value: Any) -> str:
    """Return a safe statistic-id component."""
    text = re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")
    return text or "unknown"


def statistic_id(data: Mapping[str, Any]) -> str:
    """Return the external statistic ID for one sensor."""
    return (
        f"{DOMAIN}:{_id_part(data.get('customer_number'))}_"
        f"{_id_part(data.get('estate_id'))}_"
        f"{_id_part(data.get('installation_id'))}_"
        f"{_id_part(data.get('meter_id'))}_"
        f"{_id_part(data.get('sensor_key'))}_consumption"
    )


def metadata(data: Mapping[str, Any]) -> StatisticMetaData:
    """Build statistic metadata for one sensor."""
    sensor_key = str(data.get("sensor_key") or "")
    label = SENSOR_LABELS.get(sensor_key, sensor_key.title())
    name = f"{data.get('estate_name') or 'ASFyn'} {label} consumption"

    meta: dict[str, Any] = {
        # has_mean is still required by older Home Assistant releases.
        "has_mean": False,
        "has_sum": True,
        "name": name,
        "source": DOMAIN,
        "statistic_id": statistic_id(data),
        "unit_of_measurement": data.get("unit"),
    }
    if "unit_class" in _META_KEYS:
        meta["unit_class"] = UNIT_CLASSES.get(sensor_key)
    if "mean_type" in _META_KEYS and StatisticMeanType is not None:
        meta["mean_type"] = StatisticMeanType.NONE
    return cast(StatisticMetaData, meta)


def parse_local_datetime(value: Any) -> datetime | None:
    """Parse an API date and attach the Home Assistant local timezone."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    parsed = dt_util.parse_datetime(text)
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return parsed


def parse_items(
    items: Iterable[Mapping[str, Any]],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[tuple[datetime, float]]:
    """Convert consumption items into UTC (start, value) points."""
    points: dict[datetime, float] = {}

    for item in items:
        value = as_float(item.get("value"))
        if value is None or value < 0:
            continue

        local_dt = parse_local_datetime(item.get("date"))
        if local_dt is None:
            continue

        local_date = local_dt.date()
        if start_date is not None and local_date < start_date:
            continue
        if end_date is not None and local_date >= end_date:
            continue

        points[dt_util.as_utc(local_dt)] = value

    return sorted(points.items())


def build_rows(
    points: Sequence[tuple[datetime, float]], start_sum: float
) -> list[StatisticData]:
    """Build cumulative statistic rows from per-interval consumption."""
    running_sum = start_sum
    rows: list[StatisticData] = []
    for start, value in points:
        running_sum += value
        rows.append(StatisticData(start=start, sum=running_sum))
    return rows


async def async_get_sum_before(
    hass: HomeAssistant,
    stat_id: str,
    first_start: datetime,
) -> float:
    """Return the cumulative sum immediately before ``first_start``."""
    recorder = get_instance(hass)

    last_stats = await recorder.async_add_executor_job(
        get_last_statistics, hass, 1, stat_id, False, {"sum"}
    )
    last_row = (last_stats.get(stat_id) or [None])[0]
    if last_row is None:
        return 0.0

    last_start = dt_util.utc_from_timestamp(last_row["start"])
    last_sum = float(last_row["sum"] or 0.0)
    if last_start < first_start:
        return last_sum

    rows = await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        first_start - timedelta(days=400),
        first_start - timedelta(seconds=1),
        {stat_id},
        "hour",
        None,
        {"sum"},
    )
    previous = rows.get(stat_id) or []
    if not previous:
        return 0.0
    return float(previous[-1]["sum"] or 0.0)


async def async_import_points(
    hass: HomeAssistant,
    data: Mapping[str, Any],
    points: Sequence[tuple[datetime, float]],
    *,
    period: Literal["hour", "day"],
) -> int:
    """Import per-interval consumption as cumulative external statistics."""
    if not points or not data.get("unit"):
        return 0

    stat_id = statistic_id(data)
    start_sum = await async_get_sum_before(hass, stat_id, points[0][0])
    rows = build_rows(points, start_sum)

    async_add_external_statistics(hass, metadata(data), rows)
    _LOGGER.debug(
        "Imported %s %s statistic rows for %s from %s to %s",
        len(rows),
        period,
        stat_id,
        points[0][0],
        points[-1][0],
    )
    return len(rows)
