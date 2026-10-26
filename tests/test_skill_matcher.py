import re

import pytest

from skill_matcher import find_skills
from skills_taxonomy import SKILLS


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Experience with Amazon Web Services", "AWS"),
        ("Hands-on PySpark development", "Spark"),
        ("Dashboards in PowerBI", "Power BI"),
        ("Strong T-SQL skills", "SQL Server"),
        ("Dimensional modelling and star schema design", "Data Modeling"),
        ("Exposure to LLMs and GenAI", "Generative AI"),
    ],
)
def test_aliases_resolve_to_canonical_skill(text, expected):
    assert expected in find_skills(text)


@pytest.mark.parametrize(
    "text, not_expected",
    [
        ("Frontend work in JavaScript", "Java"),
        ("Build scalable pipelines", "Scala"),
        ("Write HTML templates", "Machine Learning"),
        ("Digital transformation programs", "Git"),
    ],
)
def test_partial_words_do_not_match(text, not_expected):
    assert not_expected not in find_skills(text)


def test_matching_ignores_case():
    assert find_skills("PYTHON and sql") == find_skills("python and SQL")


def test_text_without_skills_returns_empty_list():
    assert find_skills("Great team culture and benefits") == []


def test_every_skill_has_category_and_valid_aliases():
    for name, (category, aliases) in SKILLS.items():
        assert category, f"{name} has no category"
        assert aliases, f"{name} has no aliases"
        for alias in aliases:
            re.compile(alias)


def test_no_alias_is_shared_by_two_skills():
    seen = {}
    for name, (_, aliases) in SKILLS.items():
        for alias in aliases:
            assert alias not in seen, f"'{alias}' used by {seen.get(alias)} and {name}"
            seen[alias] = name