"""Browser queries: filtering, ordering and paging.

This is the layer the UI leans on for every keystroke, so the behaviour that
matters is correctness of the filter combinations and that paging is stable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from crate.domain import Sample, SampleQuery, SampleSort, SampleStatus
from crate.library.scanner import LibraryScanner
from crate.persistence.sample_repository import SampleRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def indexed(scanner: LibraryScanner, repository: SampleRepository, library: Path) -> Path:
    scanner.scan(library)
    return library


def names(samples: list[Sample]) -> list[str]:
    return [sample.filename for sample in samples]


def test_empty_query_returns_everything_present(
    indexed: Path, repository: SampleRepository
) -> None:
    found = repository.search(SampleQuery(), limit=100)
    assert len(found) == 7
    assert repository.count_matching(SampleQuery()) == 7


def test_text_matches_filename(indexed: Path, repository: SampleRepository) -> None:
    found = repository.search(SampleQuery(text="808"), limit=100)
    assert names(found) == ["808_new.flac"]


def test_text_matches_folder_names(indexed: Path, repository: SampleRepository) -> None:
    """Producers organise by folder; the search box has to respect that."""
    found = repository.search(SampleQuery(text="kicks"), limit=100)
    assert sorted(names(found)) == ["DRM_094.WAV", "Kick_Final_2.wav"]


def test_all_terms_must_match(indexed: Path, repository: SampleRepository) -> None:
    assert names(repository.search(SampleQuery(text="kick final"), limit=100)) == [
        "Kick_Final_2.wav"
    ]
    assert repository.search(SampleQuery(text="kick nonsense"), limit=100) == []


def test_text_is_case_insensitive(indexed: Path, repository: SampleRepository) -> None:
    assert len(repository.search(SampleQuery(text="KICK"), limit=100)) == 3


def test_underscore_is_not_a_wildcard(indexed: Path, repository: SampleRepository) -> None:
    """`_` is everywhere in sample names; treating it as LIKE's any-char would
    make searches quietly wrong."""
    assert names(repository.search(SampleQuery(text="808_new"), limit=100)) == ["808_new.flac"]
    assert repository.search(SampleQuery(text="808xnew"), limit=100) == []


def test_percent_is_literal(indexed: Path, repository: SampleRepository) -> None:
    assert repository.search(SampleQuery(text="%"), limit=100) == []


def test_filter_by_extension(indexed: Path, repository: SampleRepository) -> None:
    found = repository.search(SampleQuery(extensions=frozenset({".wav"})), limit=100)
    assert {sample.extension for sample in found} == {".wav"}
    assert len(found) == 4


def test_filter_by_several_extensions(indexed: Path, repository: SampleRepository) -> None:
    query = SampleQuery(extensions=frozenset({".mp3", ".flac"}))
    assert len(repository.search(query, limit=100)) == 2


def test_missing_samples_are_hidden_by_default(
    indexed: Path, repository: SampleRepository, scanner: LibraryScanner
) -> None:
    (indexed / "vocals" / "Audio_338.mp3").unlink()
    scanner.scan(indexed)

    assert repository.count_matching(SampleQuery()) == 6
    assert repository.count_matching(SampleQuery(include_missing=True)) == 7
    hidden = repository.search(SampleQuery(include_missing=True), limit=100)
    assert any(sample.status is SampleStatus.MISSING for sample in hidden)


def test_favorites_filter(indexed: Path, repository: SampleRepository) -> None:
    target = repository.search(SampleQuery(text="808"), limit=1)[0]
    repository.set_favorite(target.id, True, NOW)

    found = repository.search(SampleQuery(favorites_only=True), limit=100)
    assert names(found) == ["808_new.flac"]
    assert found[0].favorited_at == NOW


def test_unfavoriting_clears_the_timestamp(indexed: Path, repository: SampleRepository) -> None:
    target = repository.search(SampleQuery(text="808"), limit=1)[0]
    repository.set_favorite(target.id, True, NOW)
    repository.set_favorite(target.id, False, NOW)

    reloaded = repository.get_sample(target.id)
    assert reloaded is not None
    assert reloaded.favorite is False
    assert reloaded.favorited_at is None


def test_filters_combine(indexed: Path, repository: SampleRepository) -> None:
    query = SampleQuery(text="kick", extensions=frozenset({".wav"}))
    assert len(repository.search(query, limit=100)) == 3


def test_sort_by_size(indexed: Path, repository: SampleRepository) -> None:
    ascending = repository.search(SampleQuery(sort=SampleSort.SIZE), limit=100)
    sizes = [sample.size_bytes for sample in ascending]
    assert sizes == sorted(sizes)

    descending = repository.search(SampleQuery(sort=SampleSort.SIZE, descending=True), limit=100)
    assert [s.size_bytes for s in descending] == sorted(sizes, reverse=True)


def test_sort_by_filename_ignores_case(indexed: Path, repository: SampleRepository) -> None:
    found = names(repository.search(SampleQuery(sort=SampleSort.FILENAME), limit=100))
    assert found == sorted(found, key=str.lower)


def test_paging_covers_every_row_exactly_once(indexed: Path, repository: SampleRepository) -> None:
    query = SampleQuery()
    total = repository.count_matching(query)

    seen: list[int] = []
    for offset in range(0, total, 3):
        seen.extend(sample.id for sample in repository.search(query, limit=3, offset=offset))

    assert len(seen) == total
    assert len(set(seen)) == total


def test_paging_is_stable_when_the_sort_key_ties(
    repository: SampleRepository, tmp_path: Path, scanner: LibraryScanner
) -> None:
    """Every one-shot in a pack shares an mtime; without a tiebreaker, paging
    by date would show some rows twice and skip others."""
    root = tmp_path / "pack"
    root.mkdir()
    for index in range(12):
        (root / f"hit_{index:02d}.wav").write_bytes(b"x" * 100)
    scanner.scan(root)

    query = SampleQuery(sort=SampleSort.MODIFIED)
    paged: list[int] = []
    for offset in range(0, 12, 4):
        paged.extend(s.id for s in repository.search(query, limit=4, offset=offset))

    assert len(set(paged)) == 12


def test_offset_past_the_end_is_empty(indexed: Path, repository: SampleRepository) -> None:
    assert repository.search(SampleQuery(), limit=10, offset=1000) == []


def test_extensions_in_use(indexed: Path, repository: SampleRepository) -> None:
    assert set(repository.extensions_in_use()) == {".wav", ".aiff", ".flac", ".mp3"}


def test_query_terms_ignore_extra_whitespace() -> None:
    assert SampleQuery(text="  dark   kick ").terms == ("dark", "kick")
    assert SampleQuery(text="   ").terms == ()
