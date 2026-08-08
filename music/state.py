"""Minimal process-local state for BetterKyle's one Discord server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from config import MUSIC_DEFAULT_VOLUME
from music.radio_stations import RadioStation

if TYPE_CHECKING:
    import wavelink


@dataclass(slots=True)
class MusicState:
    """Track the sole Wavelink player and whether it is in radio mode."""

    player: wavelink.Player | None = None
    is_radio: bool = False
    radio_station: RadioStation | None = None
    radio_text_channel_id: int | None = None
    radio_playback_token: str | None = None
    volume: int = MUSIC_DEFAULT_VOLUME

    @property
    def can_skip(self) -> bool:
        """Live radio has no meaningful next track."""

        return not self.is_radio

    def attach_player(self, player: wavelink.Player) -> None:
        """Remember the one active voice player."""

        self.player = player

    def enter_music_mode(self) -> None:
        """Clear only radio metadata when normal queued playback takes over."""

        self.is_radio = False
        self.radio_station = None
        self.radio_text_channel_id = None
        self.radio_playback_token = None

    def enter_radio_mode(
        self,
        station: RadioStation,
        text_channel_id: int,
        playback_token: str,
    ) -> None:
        """Record the active station after the normal queue is cleared."""

        self.is_radio = True
        self.radio_station = station
        self.radio_text_channel_id = text_channel_id
        self.radio_playback_token = playback_token

    def reset(self, *, keep_player: bool = False) -> None:
        """Clear transient mode/player data while preserving the chosen volume."""

        if not keep_player:
            self.player = None
        self.enter_music_mode()
