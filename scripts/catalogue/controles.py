import re


FAMILY_OPTIONAL = ("parent", "homepage", "icon_source", "uses", "ram_mb", "ram_source")


# Closed lists: clients translate these names, so a typo would show as a raw identifier.
DESKTOPS = (
    "gnome", "kde", "xfce", "cinnamon", "mate", "lxqt", "lxde", "budgie", "cosmic", "pantheon",
    "deepin", "enlightenment", "i3", "sway", "pixel", "unity", "ukui", "none", "choice",
)


KINDS = ("live", "installer", "netinst", "preinstalled", "rescue")


MEDIA = ("usb", "sd", "optical")


def memory_errors(where, entry):
    if "ram_mb" not in entry and "ram_source" not in entry:
        return []
    ram, source = entry.get("ram_mb"), entry.get("ram_source", "")
    if not isinstance(ram, int) or ram <= 0 or not source.startswith("https://"):
        return [f"{where}: ram_mb needs a positive whole number and an https ram_source"]
    return []


def taxonomy_errors(taxonomy):
    """Each list in menu order, every name labelled in both languages the clients show."""
    errors = []
    for kind in ("uses", "bases"):
        terms = taxonomy.get(kind, [])
        if not terms:
            errors.append(f"taxonomy: no {kind}")
        ids = [term.get("id") for term in terms]
        errors += [f"taxonomy: {kind} {name} listed twice" for name in sorted({i for i in ids if ids.count(i) > 1})]
        for term in terms:
            if not (isinstance(term.get("id"), str) and re.fullmatch(r"[a-z]+", term["id"])):
                errors.append(f"taxonomy: {kind} {term.get('id')!r} is not a lowercase name")
            errors += [f"taxonomy: {kind} {term.get('id')} has no {lang} label"
                       for lang in ("en", "fr") if not term.get(lang)]
    return errors


def source_errors(families, sources, taxonomy):
    """Mistakes a reviewer would miss: unknown names in the closed lists, memory without its source."""
    errors, known = taxonomy_errors(taxonomy), {family["id"] for family in families}
    seen = set()
    for source in sources:
        if source["id"] in seen:
            errors.append(f"image {source['id']}: duplicate identifier")
        seen.add(source["id"])
    uses = {term.get("id") for term in taxonomy.get("uses", [])}
    bases = {term.get("id") for term in taxonomy.get("bases", [])}
    for family in families:
        where = f"family {family['id']}"
        errors += [f"{where}: unknown use {use}" for use in family.get("uses", []) if use not in uses]
        if family.get("category") not in bases:
            errors.append(f"{where}: unknown base {family.get('category')}")
        errors += memory_errors(where, family)
    for source in sources:
        where = f"image {source['id']}"
        if source["family"] not in known:
            errors.append(f"{where}: unknown family {source['family']}")
        for key, allowed in (("desktop", DESKTOPS), ("kind", KINDS), ("media", MEDIA)):
            if key in source and source[key] not in allowed:
                errors.append(f"{where}: unknown {key} {source[key]}")
        errors += memory_errors(where, source)
        # Bonk reads these as booleans: a quoted "true" would leave the image unwritable.
        for key in ("vault", "hybrid"):
            if key in source and not isinstance(source[key], bool):
                errors.append(f"{where}: {key} must be true or false")
    return errors
