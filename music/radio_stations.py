"""Source-controlled registry of public live internet radio stations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RadioStation:
    """Metadata and direct stream location for one configured station."""

    key: str
    name: str
    frequency: str
    callsign: str
    stream_url: str
    stream_type: str


RADIO_STATIONS: dict[str, RadioStation] = {
    "live105": RadioStation(
        key="live105",
        name="LIVE 105",
        frequency="105.3",
        callsign="KITS",
        stream_url="https://live.amperwave.net/direct/audacy-kitsfmaac-imc",
        stream_type="AAC/live HTTP stream",
    )
}

DEFAULT_RADIO_STATION_KEY = "live105"


def get_radio_station(key: str) -> RadioStation | None:
    """Look up a station key case-insensitively."""

    return RADIO_STATIONS.get(key.strip().lower())
