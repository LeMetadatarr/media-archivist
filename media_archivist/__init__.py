from media_archivist.exceptions import MediaArchivistError, VideoUnavailable
from media_archivist.index import Index
from media_archivist.ia import IAArchivist
from media_archivist.music import YoutubeMusicArchivist
from media_archivist.version import __version__
from media_archivist.youtube import YoutubeArchivist, YoutubeMonitor

# Optional backends — only loaded if their underlying client is installed.
try:
    from media_archivist.bandcamp import BandcampArchivist  # noqa: F401
except Exception:  # pragma: no cover
    BandcampArchivist = None  # type: ignore
try:
    from media_archivist.soundcloud import SoundCloudArchivist  # noqa: F401
except Exception:  # pragma: no cover
    SoundCloudArchivist = None  # type: ignore

__all__ = [
    "YoutubeArchivist",
    "YoutubeMonitor",
    "YoutubeMusicArchivist",
    "IAArchivist",
    "BandcampArchivist",
    "SoundCloudArchivist",
    "Index",
    "MediaArchivistError",
    "VideoUnavailable",
    "__version__",
]
