import re

# Same rules as Bonk's reader (bonk-catalog/src/manifest/verify.rs, link.rs): one failing entry makes Bonk
# refuse the whole catalog, so a catalog that breaks one is never signed.
IMAGE_ID = re.compile(r"[a-z0-9._][a-z0-9._-]{0,127}")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
SHA256 = re.compile(r"[0-9a-fA-F]{64}")
FAMILY_ID = re.compile(r"[a-z][a-z0-9_]{0,31}")
FAMILY_NAME = re.compile(r"[A-Za-z0-9 ,.()+_-]{1,48}")
LABEL_PREFIX = re.compile(r"[A-Za-z0-9_-]{1,32}")
PATH_PART = re.compile(r"[A-Za-z0-9._-]+")
TOKEN = re.compile(r"[A-Za-z0-9._:/=+,-]{1,200}")
PLACEHOLDERS = ("{path}", "{label}", "{offset}", "{vault}", "{bonk}", "{cpu}")


def relative(path):
    return bool(path) and not path.startswith("/") and ":" not in path and all(
        part not in ("", "..") for part in path.split("/")
    )


def mirror(address):
    onion = address.startswith("http://") and address[len("http://"):].split("/")[0].endswith(".onion")
    return address.startswith("https://") or onion


def family_errors(family):
    if not IMAGE_ID.fullmatch(family["id"]):
        yield "identifier must be lowercase ASCII"
    if not relative(family.get("icon", "")):
        yield "icon must be a relative path"
    if not SHA256.fullmatch(family.get("icon_sha256", "")):
        yield "icon sha256 must be 64 hexadecimal characters"


def image_errors(image):
    if not IMAGE_ID.fullmatch(image["id"]):
        yield "identifier must be lowercase ASCII"
    if not DATE.fullmatch(image.get("released", "")):
        yield "release date must be YYYY-MM-DD"
    if not image.get("homepage", "").startswith("https://"):
        yield "homepage must be https"
    if not all(mirror(address) for address in image.get("mirrors", [])):
        yield "mirror must be https or an onion address"
    if "official_page" in image:
        if not image["official_page"].startswith("https://"):
            yield "official page must be https"
    else:
        if not image.get("url", "").startswith("https://"):
            yield "url must be https"
        if not SHA256.fullmatch(image.get("sha256", "")):
            yield "sha256 must be 64 hexadecimal characters"


def vault_path(path, cpu):
    plain = path.replace("{cpu}", "x") if cpu else path
    return bool(plain) and all(part != ".." and PATH_PART.fullmatch(part) for part in plain.split("/"))


def vault_token(token):
    for placeholder in PLACEHOLDERS:
        token = token.replace(placeholder, "x")
    return bool(TOKEN.fullmatch(token))


def vault_family_errors(family):
    """Same rules as bonk_core::vault::family_errors: these values are written into the GRUB menu."""
    if not FAMILY_ID.fullmatch(family.get("id", "")):
        yield "identifier must be lowercase letters, digits and _"
    if not FAMILY_NAME.fullmatch(family.get("name", "")):
        yield "name has a character the menu cannot show"
    detect = family.get("detect") or []
    if not detect or not all(paths and all(vault_path(path, False) for path in paths) for paths in detect):
        yield "detect needs sets of plain paths"
    if "label_prefix" in family and not LABEL_PREFIX.fullmatch(family["label_prefix"]):
        yield "label prefix must be letters, digits, - or _"
    if not (vault_path(family.get("kernel", ""), True) and vault_path(family.get("initrd", ""), True)):
        yield "kernel and initrd need plain paths"
    options = family.get("options", "")
    if not options or not all(vault_token(token) for token in options.split(" ")):
        yield "options need single-spaced plain tokens"
    if "initrd_extra" in family and not vault_token(family["initrd_extra"]):
        yield "initrd extra must be one plain token"


def catalog_errors(catalog):
    errors, families, images = [], set(), set()
    for family in catalog["families"]:
        errors += [f"family {family['id']}: {reason}" for reason in family_errors(family)]
        if family["id"] in families:
            errors.append(f"family {family['id']}: duplicate identifier")
        families.add(family["id"])
    for image in catalog["images"]:
        if image["family"] not in families:
            errors.append(f"image {image['id']}: unknown family")
        errors += [f"image {image['id']}: {reason}" for reason in image_errors(image)]
        if image["id"] in images:
            errors.append(f"image {image['id']}: duplicate identifier")
        images.add(image["id"])
    families = set()
    for family in catalog.get("vault_families", []):
        errors += [f"vault family {family.get('id')}: {reason}" for reason in vault_family_errors(family)]
        if family.get("id") in families:
            errors.append(f"vault family {family.get('id')}: duplicate identifier")
        families.add(family.get("id"))
    return errors
