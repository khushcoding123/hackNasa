"""Acquisition helpers. Completed raw files are immutable; partials are resumable."""

from datetime import datetime, timezone
import hashlib
import json
import math
import ntpath
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
from urllib.parse import quote

import requests

from . import paths

UNVERIFIED = "unverified: compare with the Zenodo record page"
METADATA_SUFFIXES = {".txt", ".json", ".yaml", ".yml", ".csv", ".md", ".xml"}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_checksums(path: Path) -> dict[str, str]:
    """Read sha256sum text (binary/text markers); never guess a missing digest."""
    result = {}
    if not path.is_file():
        return result
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line)
        if not match:
            raise ValueError(f"Malformed SHA256SUMS line {number}: {path}")
        digest, name = match.groups()
        name = name.removeprefix("./")
        if name in result and result[name] != digest.lower():
            raise ValueError(f"Conflicting checksums for {name}")
        result[name] = digest.lower()
    return result


def check_sha256(path: Path, expected: str | None) -> tuple[str, bool | str]:
    digest = sha256_file(path)
    if expected is None:
        return digest, "unverified"
    if digest != expected.lower():
        raise ValueError(f"SHA256 mismatch for {path}: expected {expected}, got {digest}")
    return digest, True


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
    temporary.replace(path)


def update_manifest(path: Path, entry: dict):
    """Upsert by resolved local path, preserving time and enforcing unique file IDs.

    The CLI is a single writer: simultaneous acquisition processes are unsupported.
    A content hash alone is not evidence of verification against a publisher.
    """
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "schema_version": 1, "entries": [],
    }
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("entries"), list):
        raise ValueError(f"Unsupported manifest schema: {path}")
    entry = dict(entry, local_path=str(Path(entry["local_path"]).resolve()))
    for previous in manifest["entries"]:
        if (previous["source_id"] == entry["source_id"]
                and Path(previous["local_path"]).resolve() != Path(entry["local_path"])):
            raise ValueError(f"Source ID already belongs to another file: {entry['source_id']}")
    for index, previous in enumerate(manifest["entries"]):
        if Path(previous["local_path"]).resolve() == Path(entry["local_path"]):
            if previous.get("sha256") == entry["sha256"] and previous.get("downloaded_utc"):
                entry["downloaded_utc"] = previous["downloaded_utc"]
                entry["notes"] = [note for note in entry.get("notes", [])
                                  if "original download time" not in note]
            manifest["entries"][index] = entry
            break
    else:
        manifest["entries"].append(entry)
    atomic_json(path, manifest)
    return entry


def file_source_id(source: str, path: Path) -> str:
    """Stable per-file IDs; keep the source family separate for config lookup."""
    name = path.name.lower()
    if source == "lunarrf":
        if name.endswith(".tar"):
            return "lunarrf_" + name[:-4].removeprefix("lunarrf_").removeprefix("atlas_")
        return "lunarrf_" + name.replace(".", "_")
    if source == "pgda78":
        sites = {"haworth": "haworth", "site23": "malapert", "site06": "nobile-rim-1"}
        for folder, site in sites.items():
            if name == f"{folder}_final_adj_5mpp_surf.tif":
                return f"pgda78_{site}_5m"
    return source + "_" + name.replace(".", "_")


def safe_name(name: str) -> str:
    if (not name or name in {".", ".."} or "/" in name or "\\" in name
            or any(character in name for character in ':<>"|?*')
            or name.endswith((".", " ")) or ntpath.isreserved(name)):
        raise ValueError(f"Expected a plain file/site name: {name!r}")
    return name


class Downloader:
    def __init__(self, config: dict, manifest: Path, *, session=None, log=print):
        self.config = config
        self.manifest = manifest
        self.session = session if session is not None else requests.Session()
        self.log = log

    def head_size(self, url: str) -> int | None:
        try:
            with self.session.head(url, allow_redirects=True,
                                   headers={"Accept-Encoding": "identity"},
                                   timeout=self.config["timeout_seconds"]) as response:
                response.raise_for_status()
                size = response.headers.get("Content-Length")
                size = int(size) if size is not None else None
                if size is not None and size < 0:
                    raise ValueError("Negative Content-Length")
        except (requests.RequestException, ValueError) as exc:
            self.log(f"HEAD size unavailable: {exc}")
            size = None
        self.log(f"{url}: {size if size is not None else 'unknown'} bytes")
        return size

    def record(self, path, source, url, *, digest=None, verified="unverified",
               downloaded_utc=None, **extra):
        info = self.config[source]
        version = extra.pop("version", info["version"])
        notes = []
        if version is None:
            notes.append("TODO: inspect source metadata for version")
        if downloaded_utc is None:
            notes.append("TODO: original download time is unknown for this existing file")
        if verified == "unverified":
            notes.append(UNVERIFIED if source == "lunarrf" else
                         "TODO: compare with a publisher checksum; local SHA256 is inventory only")
        entry = {
            "source_id": file_source_id(source, path), "url": url, "provider": info["provider"],
            "version": version, "license": info["license"],
            "sha256": digest or sha256_file(path), "bytes": path.stat().st_size,
            "downloaded_utc": downloaded_utc, "local_path": str(path.resolve()),
            "verified": verified, "notes": notes, **extra,
        }
        if source == "lunarrf":
            entry["label_provenance"] = "simulated"
        entry = update_manifest(self.manifest, entry)
        for note in entry["notes"]:
            self.log(f"{path.name}: {note}")
        return entry

    def download(self, url: str, destination: Path, source: str, *, expected=None, yes=False):
        size = self.head_size(url)
        if destination.exists():
            if size is not None and destination.stat().st_size != size:
                raise ValueError(f"Existing raw file has wrong size; left untouched: {destination}")
            digest, verified = check_sha256(destination, expected)
            self.log(f"Reusing {destination}")
            return self.record(destination, source, url, digest=digest, verified=verified)
        if not yes and (size is None or size > self.config["confirmation_bytes"]):
            raise ValueError("Size exceeds 2 GB or is unknown. Inspect the source, then rerun with --yes.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".part")
        offset = partial.stat().st_size if partial.exists() else 0
        if size is not None and offset > size:
            raise ValueError(f"Partial is larger than remote file; inspect {partial}")
        if not partial.exists() or size is None or offset != size:
            headers = {"Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            with self.session.get(url, headers=headers, stream=True,
                                  timeout=self.config["timeout_seconds"]) as response:
                response.raise_for_status()
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise ValueError("Encoded HTTP response cannot be safely resumed")
                length = response.headers.get("Content-Length")
                length = int(length) if length is not None else None
                total = size
                if response.status_code == 206:
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)",
                                         response.headers.get("Content-Range", ""))
                    if not match:
                        raise ValueError("Missing/invalid Content-Range; partial left untouched")
                    start, end, total = map(int, match.groups())
                    if (start != offset or end < start or end != total - 1
                            or (size is not None and size != total)
                            or (length is not None and length != end - start + 1)):
                        raise ValueError("Inconsistent Content-Range; partial left untouched")
                elif response.status_code == 200:
                    if offset:
                        self.log("Server ignored Range; restarting the partial file from byte zero")
                    offset = 0
                    if size is not None and length is not None and size != length:
                        raise ValueError("HEAD and GET sizes differ; retry after inspecting source")
                    total = length if length is not None else size
                else:
                    raise ValueError(f"Unexpected download HTTP status {response.status_code}")
                if not yes and (total is None or total > self.config["confirmation_bytes"]):
                    raise ValueError("GET size exceeds 2 GB or is unknown; rerun with --yes")
                with partial.open("ab" if offset else "wb") as target:
                    for chunk in response.iter_content(chunk_size=self.config["chunk_bytes"]):
                        if not chunk:
                            continue
                        if total is not None and target.tell() + len(chunk) > total:
                            raise ValueError("Response exceeds advertised size")
                        target.write(chunk)
                if total is not None and partial.stat().st_size != total:
                    raise ValueError(f"Incomplete download retained for resume: {partial}")
        digest, verified = check_sha256(partial, expected)
        # rename (not replace) refuses to overwrite an existing destination on Windows.
        if destination.exists():
            raise FileExistsError(destination)
        partial.rename(destination)
        return self.record(destination, source, url, digest=digest, verified=verified,
                           downloaded_utc=utc_now())


def lunarrf_url(config, name):
    return config["lunarrf"]["base_url"] + quote(name) + "?download=1"


def extract_archives(archives: list[Path], destination: Path, site: str, band: str,
                     context_name: str, *, log=print):
    """Select the site's labels and context terrain, plus legacy context metadata."""
    safe_name(site)
    safe_name(band)

    def select(archive, member):
        relative = PurePosixPath(member.name)
        wanted = (relative.name.startswith(f"{site}__") and
                  (band in relative.name or "los_count" in relative.name))
        context = (archive.name == context_name and
                   (relative.suffix.lower() in METADATA_SUFFIXES or
                    relative.name in {f"{site}__dem.tif", f"{site}__psr.tif"}))
        return wanted or context

    return _extract_selected(archives, destination, select, log=log)


def extract_metadata(archives: list[Path], destination: Path, validation_name: str, *, log=print):
    """Extract metadata from every archive, plus validation PNG/PDF figures."""
    def select(archive, member):
        suffix = PurePosixPath(member.name).suffix.lower()
        return (suffix in METADATA_SUFFIXES or
                (archive.name == validation_name and suffix in {".png", ".pdf"}))

    return _extract_selected(archives, destination, select, log=log)


def _extract_selected(archives, destination, select, *, log):
    """List all members first; manually copy only regular, confined selected files."""
    selected = []
    for archive in archives:
        with tarfile.open(archive, "r:*") as source:
            for member in source:
                log(f"{archive.name}: {member.name} ({member.size} bytes)")
                if not select(archive, member):
                    continue
                if not member.isfile():
                    if member.isdir():
                        continue
                    raise ValueError(f"Refusing selected link/special tar member: {member.name}")
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError(f"Unsafe tar member: {member.name}")
                for part in relative.parts:
                    safe_name(part)
                target = destination.joinpath(*relative.parts).resolve()
                if not target.is_relative_to(destination.resolve()):
                    raise ValueError(f"Tar member escapes extraction directory: {member.name}")
                selected.append((archive, member, target))
    if not selected:
        raise ValueError("No matching files. Inspect MANIFEST.txt and the downloaded archives.")
    outputs = []
    # Preserve relative archive paths; conflicting files are never silently replaced.
    for archive, member, target in selected:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:*") as source, source.extractfile(member) as stream:
            if target.exists():
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if target.stat().st_size != member.size or sha256_file(target) != digest:
                    raise ValueError(f"Extraction collision; existing file left untouched: {target}")
            else:
                temporary = target.with_name(target.name + ".part")
                with temporary.open("wb") as output:
                    shutil.copyfileobj(stream, output)
                if temporary.stat().st_size != member.size:
                    raise ValueError(f"Incomplete tar member: {member.name}")
                temporary.rename(target)
        outputs.append(str(target))
    log(f"Extracted/reused {len(outputs)} files; LunarRF labels are simulated")
    return outputs


def lunarrf_sites(manifest: Path) -> dict[str, dict[str, int]]:
    """Count exact site prefixes in region archive sections, including sideruns."""
    if not manifest.is_file():
        raise ValueError("MANIFEST.txt not found; run lunarrf-meta first")
    sites = {}
    archive = None
    for line in manifest.read_text(encoding="utf-8-sig").splitlines():
        header = re.fullmatch(r"---\s+(lunarrf_\S+\.tar)\s*", line)
        if header:
            archive = header.group(1)
        elif line and not line[0].isspace():
            archive = None
        elif archive and "_regions_" in archive and line.strip():
            member = PurePosixPath(line.strip())
            site, separator, layer = member.name.partition("__")
            if separator and site and layer and not line.rstrip().endswith("/"):
                counts = sites.setdefault(site, {})
                counts[archive] = counts.get(archive, 0) + 1
    if not sites:
        raise ValueError("No region site files found in MANIFEST.txt; inspect its archive sections")
    return {site: dict(sorted(counts.items())) for site, counts in sorted(sites.items())}


def register_lola(downloader: Downloader):
    from moonmesh import data as app_data

    for product, (dimension, _) in app_data.PRODUCTS.items():
        version = None
        label_path = None
        for suffix in (".LBL", ".IMG"):
            name = product + suffix
            cached = app_data.DATA / "terrain" / name
            url = app_data.LOLA + name
            path = cached if cached.is_file() else paths.raw_dir("lola_pds") / name
            acquired = None
            if not path.is_file():
                # Use the app implementation, but leave its cache read-only.
                app_data.download(url, path, dimension**2 * 2 if suffix == ".IMG" else None)
                acquired = utc_now()
            if suffix == ".LBL":
                label_path = path
                match = re.search(r'^PRODUCT_VERSION_ID\s*=\s*"?([^"\s]+)',
                                  path.read_text(encoding="ascii"), re.MULTILINE)
                version = match.group(1) if match else None
            downloader.record(path, "lola_pds", url, downloaded_utc=acquired,
                              version=version, version_metadata_path=str(label_path.resolve()))


def buffered_window(source, reference, buffer_m):
    """Transform bounds to the source's projected CRS, then buffer in metres."""
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window, from_bounds

    if not math.isfinite(buffer_m) or buffer_m < 0:
        raise ValueError("--buffer-m must be finite and nonnegative")
    if source.crs is None or reference.crs is None or not source.crs.is_projected:
        raise ValueError("Both rasters need CRSs and the source must use a projected CRS")
    _, metres_per_unit = source.crs.linear_units_factor
    bounds = reference.bounds
    if source.crs != reference.crs:
        bounds = transform_bounds(reference.crs, source.crs, *bounds, densify_pts=21)
    buffer = buffer_m / metres_per_unit
    left, bottom, right, top = bounds
    fractional = from_bounds(left-buffer, bottom-buffer, right+buffer, top+buffer,
                             transform=source.transform)
    c0, r0 = math.floor(fractional.col_off), math.floor(fractional.row_off)
    c1 = math.ceil(fractional.col_off + fractional.width)
    r1 = math.ceil(fractional.row_off + fractional.height)
    # Round outward and intersect; report the actual window so clipping is explicit.
    return Window(c0, r0, c1-c0, r1-r0).intersection(Window(0, 0, source.width, source.height))


def pgda90_window(downloader: Downloader, bounds_from: Path, buffer_m: float):
    import rasterio
    from rasterio.windows import Window

    url = downloader.config["pgda90"]["url"]
    size = downloader.head_size(url)
    try:
        # stream=True ensures a server ignoring Range is closed without consuming its body.
        with downloader.session.get(url, headers={"Range": "bytes=0-0", "Accept-Encoding": "identity"},
                                    stream=True, timeout=downloader.config["timeout_seconds"]) as probe:
            probe.raise_for_status()
            match = re.fullmatch(r"bytes 0-0/(\d+)", probe.headers.get("Content-Range", ""))
            if probe.status_code != 206 or not match:
                if probe.status_code == 200 and probe.headers.get("Content-Length"):
                    size = int(probe.headers["Content-Length"])
                raise ValueError("Server did not honor the one-byte HTTP Range probe")
            size = int(match.group(1))
        with rasterio.open(bounds_from) as reference, rasterio.Env(
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".TIF",
            GDAL_HTTP_TIMEOUT=str(downloader.config["timeout_seconds"]),
            GDAL_HTTP_MAX_RETRY="0", GDAL_HTTP_MULTIRANGE="NO",
        ), rasterio.open("/vsicurl/" + url) as source:
            window = buffered_window(source, reference, buffer_m)
            downloader.log(f"PGDA90 window (clipped to source): {window}")
            identity = f"{url}|{source.crs}|{source.transform}|{window}"
            key = hashlib.sha256(identity.encode()).hexdigest()[:16]
            destination = paths.interim_dir() / "pgda90" / f"LDEM_80S_20MPP_ADJ_window_{key}.tif"
            provenance = {"bounds_from": str(bounds_from.resolve()), "buffer_m": buffer_m,
                          "source_window": list(window.flatten()), "artifact_kind": "raster_window"}
            if destination.exists():
                return downloader.record(destination, "pgda90", url, **provenance)
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_name(destination.name + ".part")
            profile = source.profile.copy()
            profile.update(driver="GTiff", width=int(window.width), height=int(window.height),
                           transform=source.window_transform(window), tiled=True,
                           blockxsize=256, blockysize=256, BIGTIFF="IF_SAFER")
            with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True), rasterio.open(partial, "w", **profile) as output:
                output.update_tags(**source.tags())
                output.scales, output.offsets = source.scales, source.offsets
                for band in source.indexes:
                    output.update_tags(band, **source.tags(band))
                    description = source.descriptions[band-1]
                    if description:
                        output.set_band_description(band, description)
                    unit = source.units[band-1]
                    if unit:
                        output.set_band_unit(band, unit)
                for _, block in output.block_windows(1):
                    read_window = Window(window.col_off+block.col_off, window.row_off+block.row_off,
                                         block.width, block.height)
                    output.write(source.read(window=read_window), window=block)
                    output.write_mask(source.dataset_mask(window=read_window), window=block)
            partial.rename(destination)
        return downloader.record(destination, "pgda90", url, downloaded_utc=utc_now(), **provenance)
    except (requests.RequestException, rasterio.errors.RasterioError, ValueError) as exc:
        downloader.log(f"PGDA90 stopped: {exc}. Full file size: {size if size is not None else 'unknown'} bytes.")
        downloader.log("No full-file fallback was attempted. Use a Range-capable connection and rerun. "
                       "If necessary, inspect the PGDA 90 catalog and explicitly download the full file "
                       "yourself, then crop it locally with GIS tools. "
                       "If size is unknown, check Content-Length / the catalog before downloading.")
        raise
