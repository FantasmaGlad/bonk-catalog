"""Which vault family starts each published ISO, as the published Bonk reads it without downloading it."""

import concurrent.futures


def is_iso(image):
    return image.get("url", "").split("?", 1)[0].lower().endswith(".iso")


def measure(images, info, workers=8):
    """`bonk info <url> --json` of each ISO, None when it could not be read."""
    isos = [image for image in images if is_iso(image)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        reports = list(pool.map(lambda image: info(image["url"]), isos))
    return {image["id"]: report for image, report in zip(isos, reports)}


def merge(measures, previous, day):
    """This run's measures; an ISO not read this time keeps its last one, and is listed."""
    coverage, unreadable = {}, []
    for image_id, report in measures.items():
        # A Bonk older than the field gives no measure: absent, not "not recognised".
        if report is None or "vault" not in report:
            unreadable.append(image_id)
            if image_id in previous:
                coverage[image_id] = previous[image_id]
            continue
        family = (report["vault"] or {}).get("family")
        coverage[image_id] = {"family": family, "checked": day}
    return coverage, unreadable


def vault_of(coverage, image_id):
    family = coverage.get(image_id, {}).get("family")
    return {"vault_family": family} if family else {}


def summary(catalog):
    """ISOs a vault family starts, and ISOs in the catalog."""
    isos = [image for image in catalog["images"] if is_iso(image)]
    return sum(bool(image.get("vault_family")) for image in isos), len(isos)
