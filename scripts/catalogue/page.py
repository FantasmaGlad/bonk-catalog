import html
import time

from catalogue.couverture import is_iso, summary


def newer_section(updates):
    if not updates:
        return ""
    e = html.escape
    items = "\n".join(
        f'<li>{e(u["family"])} {e(u["version"])}: <a href="{e(u["where"])}">{e(u["where"])}</a></li>'
        for u in updates
    )
    return f"<section>\n<h2>Newer releases not yet in the catalog</h2>\n<ul>\n{items}\n</ul>\n</section>"


def size_label(size):
    """Megabytes under a gigabyte: a 17 MB image is not shown as 0.0 GB."""
    return f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{max(1, round(size / 1e6))} MB"


def status(image):
    state, checked = image.get("status", "unknown"), image.get("checked")
    # Checked at publication, whose date heads the page: repeating it on every row only widens the table.
    if state == "online":
        return "online"
    return f"{state}, last read {checked}" if checked else state


def vault_cell(image, names):
    if not is_iso(image):
        return ""
    family = image.get("vault_family")
    return names.get(family, family) if family else "not recognised"


def image_row(image, names):
    e = html.escape
    link = image.get("url") or image.get("official_page", "")
    label = link.rsplit("/", 1)[-1] or link
    size = size_label(image["size_bytes"]) if "size_bytes" in image else ""
    return (
        f'<tr><td><a href="{e(image["homepage"])}">{e(image["name"])}</a></td>'
        f"<td>{e(image['version'])}</td><td>{e(image.get('edition', ''))}</td>"
        f"<td>{e(image['arch'])}</td><td>{size}</td>"
        f'<td class="file"><a href="{e(link)}" title="{e(label)}">{e(label)}</a></td><td>{e(status(image))}</td>'
        f"<td>{e(vault_cell(image, names))}</td></tr>"
    )


def base_section(title, images, names):
    rows = "\n".join(image_row(image, names) for image in images)
    return (
        f"<section>\n<h2>{html.escape(title)}</h2>\n<table>\n<thead><tr><th>System</th><th>Version</th>"
        "<th>Edition</th><th>Architecture</th><th>Size</th><th>Download</th><th>Link</th><th>Vault</th></tr></thead>\n"
        "<tbody>\n" + rows + "\n</tbody>\n</table>\n</section>"
    )


def base_sections(catalog):
    """One table per base, in the taxonomy's order, then bases it does not name."""
    names = {family["id"]: family for family in catalog["families"]}
    vault_names = {family["id"]: family["name"] for family in catalog.get("vault_families", [])}
    bases = {base["id"]: base["en"] for base in catalog.get("taxonomy", {}).get("bases", [])}
    present = {family["category"] for family in catalog["families"]}
    sections = []
    for category in [base for base in bases if base in present] + sorted(present - bases.keys()):
        images = [
            image
            for image in catalog["images"]
            if image["family"] in names and names[image["family"]]["category"] == category
        ]
        sections.append(base_section(bases.get(category, category), images, vault_names))
    return sections


def index_page(catalog, updates=()):
    """A readable view of the catalog, for people: Bonk itself only reads the signed JSON."""
    day = lambda unix: time.strftime("%Y-%m-%d", time.gmtime(unix))
    sections = base_sections(catalog)
    recognised, isos = summary(catalog)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bonk image catalog</title>
<meta name="description" content="Signed list of the bootable images Bonk can write, with each publisher's link.">
<link rel="canonical" href="https://catalog.bonk-os.app/">
<link rel="icon" href="/logo.png">
<link rel="stylesheet" href="/site.css">
</head>
<body>
<main>
<header>
<h1>Bonk image catalog</h1>
<p class="lead">Signed list of the bootable images Bonk can write. Every link points to the publisher.</p>
</header>
<section>
<h2>API</h2>
<dl>
<dt>Catalog</dt><dd><a href="/v2/catalog.json">/v2/catalog.json</a></dd>
<dt>Updates</dt><dd><a href="/v2/updates.json">/v2/updates.json</a> (newer releases found at publishers, not yet in the catalog)</dd>
<dt>Signature</dt><dd><a href="/v2/catalog.json.sig">/v2/catalog.json.sig</a> (Ed25519, over the exact bytes)</dd>
<dt>Keys</dt><dd><a href="https://github.com/FantasmaGlad/bonk-catalog/tree/main/keys">keys/</a></dd>
<dt>Published</dt><dd>{day(catalog["sequence"])}</dd>
<dt>Expires</dt><dd>{day(catalog["expires_unix"])}</dd>
<dt>Content</dt><dd>{len(catalog["images"])} images, {len(catalog["families"])} systems</dd>
<dt>Vault</dt><dd>{recognised} of {isos} ISO images start from a Bonk vault (structure read by the published Bonk at each weekly bench)</dd>
<dt>Links</dt><dd>{sum(i.get("status") == "online" for i in catalog["images"])} online, {sum(i.get("status") == "stale" for i in catalog["images"])} stale (checked at each daily run)</dd>
</dl>
</section>
{newer_section(updates)}
{chr(10).join(sections)}
</main>
<footer>
<p><a href="https://bonk-os.app">bonk-os.app</a> · <a href="https://bonk-os.app/images/">Systems</a> · <a href="https://packages.bonk-os.app">packages</a> · <a href="https://security.bonk-os.app">security</a> · <a href="https://github.com/FantasmaGlad/bonk-catalog">GitHub</a></p>
</footer>
</body>
</html>
"""
