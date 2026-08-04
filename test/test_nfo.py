"""Tests for media_archivist.nfo — .nfo sidecar XML generation."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from media_archivist.models.canonical import MediaEntry
from media_archivist.models.raw import Source
from media_archivist.nfo import nfo_xml


def _entry(**overrides) -> MediaEntry:
    fields = dict(
        source=Source.YOUTUBE,
        url="https://youtu.be/abc",
        title="Demo Title",
        raw={},
    )
    fields.update(overrides)
    return MediaEntry.build(**fields)


def test_music_entry_produces_musicvideo_root():
    entry = _entry(source=Source.BANDCAMP, title="Avril 14th",
                    artist="Aphex Twin", album="Drukqs",
                    tags=["idm", "electronic"], thumbnail="https://x/t.jpg")
    xml = nfo_xml(entry)
    root = ET.fromstring(xml)
    assert root.tag == "musicvideo"
    assert root.findtext("title") == "Avril 14th"
    assert root.findtext("artist") == "Aphex Twin"
    assert root.findtext("album") == "Drukqs"
    assert "idm" in [g.text for g in root.findall("genre")]
    assert "idm" in [g.text for g in root.findall("tag")]
    assert root.findtext("thumb") == "https://x/t.jpg"


def test_non_music_entry_produces_movie_root():
    entry = _entry(source=Source.YOUTUBE, title="Talk", artist="Some Channel")
    xml = nfo_xml(entry)
    root = ET.fromstring(xml)
    assert root.tag == "movie"
    assert root.findtext("studio") == "Some Channel"


def test_internet_archive_entry_is_movie():
    entry = _entry(source=Source.INTERNET_ARCHIVE, title="Old Film")
    root = ET.fromstring(nfo_xml(entry))
    assert root.tag == "movie"


def test_xml_is_well_formed_and_has_header():
    entry = _entry()
    xml = nfo_xml(entry)
    assert xml.startswith("<?xml")
    # Raises if malformed.
    ET.fromstring(xml)


def test_text_is_escaped():
    entry = _entry(title="Rock & Roll <Live>")
    xml = nfo_xml(entry)
    assert "Rock & Roll <Live>" not in xml
    assert "&amp;" in xml
    root = ET.fromstring(xml)  # would raise if the escaping broke the XML
    assert root.findtext("title") == "Rock & Roll <Live>"


def test_thumbnail_present_when_set():
    entry = _entry(thumbnail="https://x/pic.jpg")
    root = ET.fromstring(nfo_xml(entry))
    assert root.findtext("thumb") == "https://x/pic.jpg"


def test_thumbnail_omitted_when_none():
    entry = _entry(thumbnail=None)
    root = ET.fromstring(nfo_xml(entry))
    assert root.find("thumb") is None


def test_runtime_and_year_derived():
    entry = _entry(duration=185.0, published="2021-05-04T00:00:00Z")
    root = ET.fromstring(nfo_xml(entry))
    assert root.findtext("runtime") == "3"
    assert root.findtext("year") == "2021"
    assert root.findtext("premiered") == "2021-05-04"


def test_no_published_omits_year_and_premiered():
    entry = _entry(published=None)
    root = ET.fromstring(nfo_xml(entry))
    assert root.find("year") is None
    assert root.find("premiered") is None
