FIRMWARE = ("bios", "uefi", "secureboot")


def bench_errors(bench, image_ids):
    for entry in bench:
        image = entry.get("image", "")
        if image not in image_ids:
            yield f"{image}: not an image of the catalog"
        if not entry.get("firmware") or any(firmware not in FIRMWARE for firmware in entry["firmware"]):
            yield f"{image}: firmware must be among {', '.join(FIRMWARE)}"
        if set(entry.get("loader", {})) != {"bios", "uefi"}:
            yield f"{image}: loader must name the BIOS and the UEFI one"
        if not entry.get("proof"):
            yield f"{image}: proof pattern missing"


def matrix(bench):
    return [
        {
            "image": entry["image"],
            "firmware": firmware,
            "loader": entry["loader"]["bios" if firmware == "bios" else "uefi"],
            "proof": entry["proof"],
            "options": " ".join(entry["options"] + (entry.get("bios_options", []) if firmware == "bios" else [])),
        }
        for entry in bench
        for firmware in entry["firmware"]
    ]


def signer_of(report):
    extraction = report.get("extraction") or {}
    if "signer" in extraction:
        return extraction["signer"]
    loaders = report.get("efi_loaders") or []
    return loaders[0]["signer"] if loaders else None


def merge(bench, results, previous, run, day):
    """Proofs of this run, the last good boot of each image kept, and the boots that failed."""
    proofs, failures = {}, []
    for entry in bench:
        image = entry["image"]
        runs = {result["firmware"]: result for result in results if result["image"] == image}
        passed = [firmware for firmware in FIRMWARE if runs.get(firmware, {}).get("ok")]
        failed = [firmware for firmware in entry["firmware"] if firmware not in passed]
        proof = dict(previous.get(image, {}))
        if passed:
            proof.update(firmware=passed, proved={"date": day, "bench": run})
        report = next((result["info"] for result in runs.values() if result.get("info")), None)
        if report:
            proof["route"] = report.get("route")
            proof["secure_boot"] = signer_of(report)
        if proof:
            proofs[image] = proof
        if failed:
            failures.append({"id": image, "error": f"boot failed: {', '.join(failed)}"})
    return proofs, failures
