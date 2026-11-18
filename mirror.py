#!/usr/bin/env python3
"""Mirror TheFirmwareHub's catalog and firmware assets."""

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import urlopen


BASE_URL = "https://thefirmwarehub.com"
MIRROR_DIR = Path("mirror")
LOG = logging.getLogger(__name__)


def firmware_list(catalog):
    if not isinstance(catalog, dict):
        raise ValueError("catalog must be an object")
    data = catalog.get("data", catalog)
    if not isinstance(data, dict):
        raise ValueError("catalog data must be an object")
    firmwares = data["firmwares"]
    if not isinstance(firmwares, list) or not all(isinstance(item, dict) for item in firmwares):
        raise ValueError("firmwares must be a list of objects")
    return firmwares


def firmware_sha256(firmware):
    sha256 = firmware["sha256"]
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        raise ValueError(f"invalid SHA-256 for firmware {firmware.get('id', '<unknown>')}")
    return sha256.lower()


def asset_location(mirror_dir, path):
    if not isinstance(path, str):
        raise ValueError("asset path must be a string")
    parts = urlsplit(path)
    if parts.scheme or parts.netloc or parts.query or parts.fragment:
        raise ValueError(f"invalid asset path: {path!r}")
    decoded = unquote(parts.path).lstrip("/")
    segments = decoded.split("/")
    if any(segment in ("", ".", "..") or "\\" in segment for segment in segments):
        raise ValueError(f"invalid asset path: {path!r}")
    destination = mirror_dir.joinpath(*segments)
    if not destination.resolve().is_relative_to(mirror_dir.resolve()):
        raise ValueError(f"asset path escapes mirror: {path!r}")
    return f"{BASE_URL}/{quote(decoded, safe='/')}", destination


def download_asset(mirror_dir, path, expected_sha256=None):
    url, destination = asset_location(mirror_dir, path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with urlopen(url, timeout=60) as response:
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
                temporary = Path(output.name)
                digest = hashlib.sha256()
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
        if expected_sha256 and digest.hexdigest() != expected_sha256.lower():
            raise ValueError(f"SHA-256 mismatch for {path}: expected {expected_sha256}, got {digest.hexdigest()}")
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_catalog(path, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def fetch_catalog(mirror_dir):
    today = date.today().isoformat()
    date_path = mirror_dir / ".catalog-fetch-date"
    pending_path = mirror_dir / ".catalog-pending.json"
    if date_path.exists() and date_path.read_text(encoding="utf-8") == today:
        if not pending_path.exists():
            raise ValueError("catalog fetch already attempted today without a saved response")
        return pending_path.read_bytes()

    pending_path.unlink(missing_ok=True)
    save_catalog(date_path, today.encode())
    with urlopen(f"{BASE_URL}/catalog.json?{today}", timeout=60) as response:
        content = response.read()
    firmware_list(json.loads(content))
    save_catalog(pending_path, content)
    return content


def mirror(mirror_dir=MIRROR_DIR):
    mirror_dir = Path(mirror_dir)
    mirror_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = mirror_dir / "catalog.json"
    previous = {}
    if catalog_path.exists():
        with catalog_path.open(encoding="utf-8") as source:
            previous = {str(item["id"]): firmware_sha256(item) for item in firmware_list(json.load(source))}

    content = fetch_catalog(mirror_dir)
    firmwares = firmware_list(json.loads(content))

    failed = False
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=mirror_dir) as temporary:
        staging = Path(temporary)
        for firmware in firmwares:
            try:
                firmware_id = str(firmware["id"])
                sha256 = firmware_sha256(firmware)
                if previous.get(firmware_id) == sha256:
                    continue

                card = firmware.get("card")
                if card:
                    try:
                        download_asset(staging, card)
                    except HTTPError as error:
                        if error.code != 404:
                            raise
                        card = None
                if not card:
                    if not re.fullmatch(r"[A-Za-z0-9_-]+", firmware_id):
                        raise ValueError(f"invalid firmware id for fallback card: {firmware_id!r}")
                    download_asset(staging, f"img/w0/{firmware_id}.webp")
                download_asset(staging, firmware["pkg"], sha256)
            except (KeyError, TypeError, ValueError, OSError, URLError) as error:
                LOG.error("Failed to mirror firmware %s: %s", firmware.get("id", "<unknown>"), error)
                failed = True

        if failed:
            LOG.error("Catalog was not updated; failed firmware will be retried on the next run")
            return False
        assets = []
        for asset in staging.rglob("*"):
            if asset.is_file():
                relative = asset.relative_to(staging).as_posix()
                _, destination = asset_location(mirror_dir, quote(relative, safe="/"))
                if destination in (catalog_path, mirror_dir / ".catalog-fetch-date", mirror_dir / ".catalog-pending.json"):
                    raise ValueError(f"asset path conflicts with catalog state: {relative}")
                assets.append((asset, destination))
        for asset, destination in assets:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(asset, destination)
        save_catalog(catalog_path, content)
    return True


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        return 0 if mirror() else 1
    except (KeyError, TypeError, ValueError, OSError, URLError) as error:
        LOG.error("Mirror failed: %s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
