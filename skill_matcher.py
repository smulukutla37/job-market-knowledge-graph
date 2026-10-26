import re

from skills_taxonomy import SKILLS

# One compiled pattern per canonical skill: all its aliases, whole words only
PATTERNS = {
    name: re.compile(r"\b(?:" + "|".join(aliases) + r")\b", re.IGNORECASE)
    for name, (_, aliases) in SKILLS.items()
}


def find_skills(text):
    """Return the canonical names of every skill whose aliases appear in the text."""
    return sorted(name for name, pattern in PATTERNS.items() if pattern.search(text))