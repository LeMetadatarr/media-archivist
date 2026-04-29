class MediaArchivistError(Exception):
    """Base class for all media_archivist errors."""


class VideoUnavailable(MediaArchivistError):
    """Raised when a video has been removed, made private, or otherwise can't be fetched."""
