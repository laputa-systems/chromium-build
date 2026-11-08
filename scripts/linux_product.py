#!/usr/bin/env python3
"""Resolve Linux product defaults, lock paths, and phase identity."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from script_support import ScriptFailure

PRODUCTS = ("chromium", "helium")
PROFILES = {
    "chromium": ("headless-debug", "headless-release"),
    "helium": ("helium-wayland-release",),
}
DEFAULT_PROFILES = {"chromium": "headless-debug", "helium": "helium-wayland-release"}
OUTPUT_NAMES = {"chromium": "chrome", "helium": "helium"}
GN_TARGETS = {"chromium": "//chrome:chrome", "helium": "//chrome:chrome"}
CONFIG_FILES = {
    "chromium": {
        "inputs": "config/inputs.lock",
        "packages": "config/packages.{arch}.lock",
        "inventory": "config/patch-inventory.json",
        "disposition": "config/patch-disposition.json",
        "policy": "config/system-library-preflight.schema.json",
    },
    "helium": {
        "inputs": "config/helium.inputs.lock",
        "packages": "config/helium.packages.{arch}.lock",
        "inventory": "config/helium.patch-inventory.json",
        "disposition": "config/helium.patch-disposition.json",
        "policy": "config/helium.system-library-preflight.schema.json",
    },
}
CACHE_VOLUMES = {
    "chromium": "ungoogled-chromium-153-ccache-{arch}",
    "helium": "ungoogled-chromium-153-ccache-{arch}",
}


@dataclass(frozen=True)
class Product:
    name: str
    arch: str
    profile: str
    chromium_version: str
    product_version: str | None
    devtools_revision: str
    image: str
    work_volume: str
    cache_volume: str
    inputs_lock: Path
    package_lock: Path
    patch_inventory: Path
    patch_disposition: Path
    policy: Path
    executable: str
    target: str
    lock_sha256: str


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def config_root() -> Path:
    return Path(os.environ.get("CHROMIUM_BUILD_ROOT", "/opt/chromium-build"))


def validate(product: str, platform: str, arch: str, profile: str | None) -> tuple[str, str]:
    if product not in PRODUCTS:
        raise ScriptFailure(f"unknown product {product}")
    if platform not in {"linux", "macos"}:
        raise ScriptFailure(f"unsupported platform {platform}")
    if arch == "aarch64":
        arch = "arm64"
    if arch not in {"arm64", "amd64"}:
        raise ScriptFailure(f"unsupported architecture {arch}")
    if product == "helium" and platform == "macos":
        raise ScriptFailure("Helium is not supported on macOS")
    profile = profile or DEFAULT_PROFILES[product]
    if profile not in PROFILES[product]:
        raise ScriptFailure(
            f"profile {profile} is not valid for product {product}; "
            f"expected one of {', '.join(PROFILES[product])}"
        )
    return arch, profile


def resolve(product: str, arch: str, profile: str | None = None, root: Path | None = None) -> Product:
    arch, profile = validate(product, "linux", arch, profile)
    root = root or config_root()
    names = CONFIG_FILES[product]
    inputs_lock = root / names["inputs"]
    if not inputs_lock.is_file():
        raise ScriptFailure(
            f"missing {inputs_lock}; product {product} locks are not installed"
        )
    lock = json.loads(inputs_lock.read_text(encoding="utf-8"))
    if lock.get("product", {}).get("name") not in (None, product):
        raise ScriptFailure(f"{inputs_lock} belongs to product {lock['product']['name']}")
    chromium_version = lock["chromium"]["version"]
    product_version = None
    if product == "helium":
        product_version = lock.get("product", {}).get("version")
        if not product_version:
            raise ScriptFailure(f"{inputs_lock} has no Helium product version")
        defaults = {
            "image": f"helium-builder:{product_version}-{arch}",
            "work": f"helium-{product_version}-work-{arch}",
        }
    else:
        defaults = {
            "image": f"ungoogled-chromium-builder:{chromium_version}-{arch}",
            "work": f"ungoogled-chromium-{chromium_version.split('.')[0]}-work-{arch}",
        }
    image = os.environ.get("CHROMIUM_BUILDER_IMAGE") or (
        os.environ.get("HELIUM_IMAGE") if product == "helium" else ""
    ) or defaults["image"]
    work_volume = os.environ.get("CHROMIUM_WORK_VOLUME") or (
        os.environ.get("HELIUM_WORK") if product == "helium" else ""
    ) or defaults["work"]
    cache_volume = os.environ.get("CHROMIUM_CACHE_VOLUME") or (
        os.environ.get("HELIUM_CACHE") if product == "helium" else ""
    ) or CACHE_VOLUMES[product].format(arch=arch)
    package_lock = root / names["packages"].format(arch=arch)
    return Product(
        name=product,
        arch=arch,
        profile=profile,
        chromium_version=chromium_version,
        product_version=product_version,
        devtools_revision=lock["chromium"]["devtools_revision"],
        image=image,
        work_volume=work_volume,
        cache_volume=cache_volume,
        inputs_lock=inputs_lock,
        package_lock=package_lock,
        patch_inventory=root / names["inventory"],
        patch_disposition=root / names["disposition"],
        policy=root / names["policy"],
        executable=OUTPUT_NAMES[product],
        target=GN_TARGETS[product],
        lock_sha256=digest(inputs_lock),
    )


def current() -> Product | None:
    product = os.environ.get("CHROMIUM_PRODUCT", "chromium")
    if product == "chromium" and not (config_root() / "config/inputs.lock").is_file():
        return None
    try:
        return resolve(
            product,
            os.environ.get("CHROMIUM_ARCH", "amd64"),
            os.environ.get("CHROMIUM_PROFILE", DEFAULT_PROFILES.get(product, "headless-debug")),
        )
    except ScriptFailure:
        return None


def identity(product: Product) -> dict[str, object]:
    return {
        "product": product.name,
        "chromium_version": product.chromium_version,
        "product_version": product.product_version,
        "architecture": product.arch,
        "profile": product.profile,
        "inputs_lock_sha256": product.lock_sha256,
    }


def require_report(path: Path, product: Product, phase: str) -> dict:
    if not path.is_file():
        raise ScriptFailure(f"{phase} has not passed for {product.name}; run {phase} first")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("status") != "complete":
        raise ScriptFailure(f"{phase} report is incomplete; rerun {phase}")
    expected = identity(product)
    for key in ("product", "chromium_version", "product_version", "architecture", "profile", "inputs_lock_sha256"):
        if key not in report:
            raise ScriptFailure(f"{phase} report has no {key}; rerun {phase}")
        if report[key] != expected[key]:
            raise ScriptFailure(
                f"{phase} report {key}={report[key]!r} does not match "
                f"{expected[key]!r}; rerun {phase}"
            )
    return report


def package_world(path: Path) -> list[str]:
    if not path.is_file():
        raise ScriptFailure(f"missing package lock {path}")
    lock = json.loads(path.read_text(encoding="utf-8"))
    if lock.get("status") not in (None, "resolved"):
        raise ScriptFailure(f"{path}: package lock is not resolved")
    world = lock.get("world")
    if not isinstance(world, list) or not world:
        raise ScriptFailure(f"{path}: world list is empty")
    for token in world:
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9+._-]+=[A-Za-z0-9._-]+", token):
            raise ScriptFailure(f"{path}: invalid package token {token!r}")
    return world


def resolve_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", required=True)
    parser.add_argument("--arch", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--package-world", action="store_true")
    args = parser.parse_args(argv)
    try:
        product = resolve(args.product, args.arch, args.profile, args.root)
        if args.package_world:
            print(" ".join(package_world(product.package_lock)))
            return 0
    except ScriptFailure as error:
        print(f"linux_product: {error}", file=sys.stderr)
        return 1
    fields = {
        "product": product.name,
        "chromium_version": product.chromium_version,
        "product_version": product.product_version or "",
        "architecture": product.arch,
        "profile": product.profile,
        "image": product.image,
        "work_volume": product.work_volume,
        "cache_volume": product.cache_volume,
        "inputs_lock": str(product.inputs_lock),
        "package_lock": str(product.package_lock),
        "patch_inventory": str(product.patch_inventory),
        "patch_disposition": str(product.patch_disposition),
        "policy": str(product.policy),
        "executable": product.executable,
        "target": product.target,
    }
    for name, value in fields.items():
        print(f"{name}\t{value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(resolve_main(sys.argv[1:]))
