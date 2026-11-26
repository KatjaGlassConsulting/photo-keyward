from __future__ import annotations

from dataclasses import dataclass

from .exif_cache import ExifData


@dataclass(frozen=True)
class KeywordFilter:
    """Which images the grid should show, given their EXIF keywords.

    ``all_selected`` is carried explicitly rather than derived: only the panel
    knows how many rows exist, and the caller uses the flag to skip building a
    predicate at all when nothing is filtered out.
    """

    selected: frozenset[str]
    include_no_key: bool
    all_selected: bool

    def accepts(self, data: ExifData | None) -> bool:
        if self.all_selected:
            return True
        if data is None:
            # EXIF not read yet, or exiftool is missing. Visible rather than
            # hidden: the folder listing is instant while the exiftool
            # subprocess is not, so hiding unknowns would flash an empty grid
            # on every folder change, and a missing exiftool would empty the
            # grid permanently with no visible cause.
            return True
        # Union rather than the field-by-field split KeywordPanel uses. That
        # panel has to pick a field because it writes to one; this only reads,
        # and the union also catches images another application tagged with a
        # flat term in HierarchicalSubject.
        keys = set(data.keywords) | set(data.hierarchical)
        if not keys:
            return self.include_no_key
        return not keys.isdisjoint(self.selected)
