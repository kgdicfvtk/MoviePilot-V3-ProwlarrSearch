from __future__ import annotations

from app.plugins.prowlarrsearch.mapper import rank_releases


def _release(title: object, *, seeders: object = 0, **extra: object) -> dict[str, object]:
    return {"title": title, "seeders": seeders, **extra}


def test_identifier_match_outranks_title_only_match() -> None:
    title_match = _release("tt0133093 remux", seeders=999)
    identifier_match = _release("The Matrix", seeders=1, imdbId=133093)

    ranked = rank_releases([title_match, identifier_match], "tt0133093")

    assert ranked == [identifier_match, title_match]


def test_tmdb_identifier_match_outranks_title_only_match() -> None:
    title_match = _release("tmdb 496243 remux", seeders=999)
    identifier_match = _release("기생충", seeders=1, tmdbId="496243")

    ranked = rank_releases([title_match, identifier_match], "tmdb:496243")

    assert ranked == [identifier_match, title_match]


def test_exact_phrase_all_tokens_and_fuzzy_tiers_are_deterministic() -> None:
    unrelated = _release("Completely Different Film", seeders=999)
    fuzzy = _release("The Matrx 1999", seeders=1)
    all_tokens = _release("1999 Remastered The Matrix", seeders=1)
    phrase = _release("The.Matrix.1999.2160p.UHD", seeders=1)
    exact = _release("ignored display title", seeders=1, sortTitle="The Matrix 1999")

    ranked = rank_releases(
        [unrelated, fuzzy, all_tokens, phrase, exact], "The Matrix 1999"
    )

    assert ranked == [exact, phrase, all_tokens, fuzzy, unrelated]


def test_chinese_punctuation_and_year_normalize_without_filtering_results() -> None:
    english = _release("Parasite.2019.KOREAN.1080p", seeders=20)
    chinese = _release("寄生虫（2019）.BluRay", seeders=2)
    malformed = {"title": None, "seeders": "bad"}

    ranked = rank_releases([english, malformed, chinese], "寄生虫 2019")

    assert ranked[0] is chinese
    assert {id(item) for item in ranked} == {id(english), id(malformed), id(chinese)}


def test_equal_similarity_uses_seeders_then_original_order() -> None:
    first = _release("Same Unrelated Title", seeders=3)
    second = _release("Same Unrelated Title", seeders=9)
    third = _release("Same Unrelated Title", seeders=9)

    assert rank_releases([first, second, third], "No Match Here") == [
        second,
        third,
        first,
    ]


def test_malformed_top_level_is_safe_and_duplicate_entries_are_preserved() -> None:
    duplicate = _release("Target")

    assert rank_releases(None, "Target") == []
    assert rank_releases([None, duplicate, duplicate, 7], "Target") == [
        duplicate,
        duplicate,
        None,
        7,
    ]
