"""All acquisition tests use temporary files and fake HTTP; no network is allowed."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import numpy as np
import pytest
import requests
import rasterio
from rasterio.transform import from_origin
import yaml

from moonmesh_train import downloads as dl, paths


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("Network is forbidden in download tests")
    monkeypatch.setattr(requests.sessions.Session, "request", refuse)


@pytest.fixture
def config():
    return yaml.safe_load((paths.repo_root() / "training/configs/downloads.yaml").read_text())


@pytest.fixture
def cli():
    spec = importlib.util.spec_from_file_location("download_cli", paths.repo_root() / "training/scripts/00_download.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Response:
    def __init__(self, body=b"", status=200, headers=None, interrupt=False):
        self.body = body
        self.status_code = status
        self.headers = headers or {}
        self.interrupt = interrupt
        self.consumed = False
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, chunk_size):
        self.consumed = True
        yield self.body
        if self.interrupt:
            raise requests.ConnectionError("interrupted")


class Session:
    def __init__(self, size, *responses):
        self.size = size
        self.responses = list(responses)
        self.calls = []

    def head(self, url, **kwargs):
        self.calls.append(("HEAD", url, kwargs))
        return Response(headers={} if self.size is None else {"Content-Length": str(self.size)})

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        assert self.responses, "Unexpected HTTP GET"
        return self.responses.pop(0)


def downloader(tmp_path, config, session):
    return dl.Downloader(config, tmp_path / "manifest.json", session=session, log=lambda _: None)


def test_checksum_formats_and_verification(tmp_path):
    path = tmp_path / "sample"
    path.write_bytes(b"abc")
    checksums = tmp_path / "SHA256SUMS"
    checksums.write_text(f"# comment\n{digest(b'abc')}  ./sample\n{digest(b'xyz').upper()} *binary\n")
    parsed = dl.read_checksums(checksums)
    assert parsed == {"sample": digest(b"abc"), "binary": digest(b"xyz")}
    assert dl.check_sha256(path, parsed["sample"]) == (digest(b"abc"), True)
    assert dl.check_sha256(path, None) == (digest(b"abc"), "unverified")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        dl.check_sha256(path, parsed["binary"])
    assert dl.read_checksums(tmp_path / "missing") == {}


@pytest.mark.parametrize("text", ["nonsense", f"{digest(b'a')} x\n{digest(b'b')} x"])
def test_invalid_checksums_rejected(tmp_path, text):
    path = tmp_path / "SHA256SUMS"
    path.write_text(text)
    with pytest.raises(ValueError):
        dl.read_checksums(path)


def test_resume_verified_download_and_manifest(tmp_path, config):
    destination = tmp_path / "archive.tar"
    destination.with_name("archive.tar.part").write_bytes(b"abc")
    session = Session(6, Response(b"def", 206, {"Content-Range": "bytes 3-5/6", "Content-Length": "3"}))
    worker = downloader(tmp_path, config, session)
    entry = worker.download("https://example.test/file", destination, "lunarrf", expected=digest(b"abcdef"))
    assert session.calls[1][2]["headers"]["Range"] == "bytes=3-"
    assert destination.read_bytes() == b"abcdef"
    assert not destination.with_name("archive.tar.part").exists()
    assert entry["verified"] is True
    assert entry["downloaded_utc"] is not None
    assert entry["bytes"] == 6
    assert entry["label_provenance"] == "simulated"
    assert json.loads(worker.manifest.read_text())["entries"] == [entry]


def test_range_ignored_restarts_without_duplicate_bytes(tmp_path, config):
    target = tmp_path / "file"
    (tmp_path / "file.part").write_bytes(b"abc")
    session = Session(6, Response(b"abcdef", headers={"Content-Length": "6"}))
    downloader(tmp_path, config, session).download("url", target, "lunarrf", expected=digest(b"abcdef"))
    assert target.read_bytes() == b"abcdef"


@pytest.mark.parametrize("content_range", [None, "bytes 2-5/6", "bytes 3-4/6", "bytes 3-6/7"])
def test_invalid_range_leaves_partial_untouched(tmp_path, config, content_range):
    partial = tmp_path / "file.part"
    partial.write_bytes(b"abc")
    headers = {} if content_range is None else {"Content-Range": content_range}
    session = Session(6, Response(b"def", 206, headers))
    with pytest.raises(ValueError, match="Content-Range"):
        downloader(tmp_path, config, session).download("url", tmp_path / "file", "lunarrf")
    assert partial.read_bytes() == b"abc"
    assert not (tmp_path / "manifest.json").exists()


def test_interruption_then_resume(tmp_path, config):
    worker = downloader(tmp_path, config, Session(6, Response(b"abc", interrupt=True)))
    target = tmp_path / "file"
    with pytest.raises(requests.ConnectionError):
        worker.download("url", target, "lunarrf", expected=digest(b"abcdef"))
    assert (tmp_path / "file.part").read_bytes() == b"abc"
    assert not worker.manifest.exists()
    worker.session = Session(6, Response(b"def", 206, {"Content-Range": "bytes 3-5/6"}))
    worker.download("url", target, "lunarrf", expected=digest(b"abcdef"))
    assert target.read_bytes() == b"abcdef"


@pytest.mark.parametrize("body,expected_error", [(b"ab", "Incomplete download"), (b"abcd", "exceeds advertised"), (b"xyz", "SHA256 mismatch")])
def test_bad_download_never_promoted(tmp_path, config, body, expected_error):
    worker = downloader(tmp_path, config, Session(3, Response(body)))
    with pytest.raises(ValueError, match=expected_error):
        worker.download("url", tmp_path / "file", "lunarrf", expected=digest(b"abc"))
    assert not (tmp_path / "file").exists()
    assert not worker.manifest.exists()


def test_complete_partial_needs_no_get(tmp_path, config):
    (tmp_path / "file.part").write_bytes(b"abc")
    session = Session(3)
    downloader(tmp_path, config, session).download("url", tmp_path / "file", "lunarrf", expected=digest(b"abc"))
    assert [call[0] for call in session.calls] == ["HEAD"]


@pytest.mark.parametrize("size", [2_000_000_001, None])
def test_large_or_unknown_size_requires_yes_before_get(tmp_path, config, size):
    session = Session(size)
    with pytest.raises(ValueError, match="--yes"):
        downloader(tmp_path, config, session).download("url", tmp_path / "file", "lunarrf")
    assert len(session.calls) == 1
    assert not (tmp_path / "file.part").exists()


def test_yes_allows_unknown_size_and_records_unverified(tmp_path, config):
    worker = downloader(tmp_path, config, Session(None, Response(b"abc")))
    entry = worker.download("url", tmp_path / "file", "lunarrf", yes=True)
    assert entry["verified"] == "unverified"
    assert dl.UNVERIFIED in entry["notes"]


def test_existing_raw_file_is_never_overwritten(tmp_path, config):
    target = tmp_path / "file"
    target.write_bytes(b"abc")
    worker = downloader(tmp_path, config, Session(3))
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        worker.download("url", target, "lunarrf", expected=digest(b"xyz"))
    assert target.read_bytes() == b"abc"
    entry = worker.download("url", target, "lunarrf", expected=digest(b"abc"))
    assert entry["downloaded_utc"] is None
    assert all(call[0] == "HEAD" for call in worker.session.calls)


def test_manifest_upsert_retains_time_and_other_entries(tmp_path, config):
    worker = downloader(tmp_path, config, Session(3, Response(b"abc")))
    target = tmp_path / "file"
    first = worker.download("url", target, "lunarrf")
    other = tmp_path / "other"
    other.write_bytes(b"other")
    worker.record(other, "pgda78", "other-url")
    again = worker.download("url", target, "lunarrf", expected=digest(b"abc"))
    entries = json.loads(worker.manifest.read_text())["entries"]
    assert len(entries) == 2
    assert again["downloaded_utc"] == first["downloaded_utc"]
    assert again["verified"] is True
    assert not any("original download time" in note for note in again["notes"])
    assert entries[1]["version"] is None
    assert entries[1]["license"] == "check catalog"
    assert not worker.manifest.with_name("manifest.json.tmp").exists()


def test_invalid_manifest_is_not_overwritten(tmp_path, config):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"schema_version": 99}')
    with pytest.raises(ValueError, match="Unsupported manifest"):
        dl.update_manifest(manifest, {})
    assert manifest.read_text() == '{"schema_version": 99}'


def make_tar(path, members):
    with tarfile.open(path, "w") as archive:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def test_extract_selection_listing_and_raw_immutability(tmp_path):
    context = tmp_path / "context.tar"
    region = tmp_path / "region.tar"
    make_tar(context, {"meta/run.JSON": b"{}", "unrelated.tif": b"no",
                       "context/regions/Haworth__dem.tif": b"dem",
                       "context/regions/Haworth__psr.tif": b"psr",
                       "context/regions/Nobile_Rim_1__dem.tif": b"no"})
    make_tar(region, {"maps/Haworth__best_prx_1p800GHz.tif": b"yes", "Haworth__los_count.tif": b"count",
                      "Haworth__best_prx_2p580GHz.tif": b"no", "Nobile__best_prx_1p800GHz.tif": b"no",
                      "regional.txt": b"no", "NotHaworth__best_prx_1p800GHz.tif": b"no"})
    before = [dl.sha256_file(path) for path in (context, region)]
    output = tmp_path / "out"
    listing = []

    def log(message):
        if not message.startswith("Extracted"):
            assert not output.exists(), "All listings must precede extraction"
        listing.append(message)

    extracted = dl.extract_archives([context, region], output, "Haworth", "1p800GHz", context.name, log=log)
    assert {Path(path).relative_to(output).as_posix() for path in extracted} == {
        "meta/run.JSON", "maps/Haworth__best_prx_1p800GHz.tif", "Haworth__los_count.tif",
        "context/regions/Haworth__dem.tif", "context/regions/Haworth__psr.tif"}
    assert len(listing) == 12
    assert before == [dl.sha256_file(path) for path in (context, region)]
    assert dl.extract_archives([context, region], output, "Haworth", "1p800GHz", context.name, log=lambda _: None) == extracted


@pytest.mark.parametrize("name", ["../escape.txt", "/escape.txt", "C:/escape.txt", "..\\escape.txt"])
def test_extract_rejects_traversal(tmp_path, name):
    archive = tmp_path / "context.tar"
    make_tar(archive, {name: b"bad"})
    with pytest.raises(ValueError):
        dl.extract_archives([archive], tmp_path / "out", "Haworth", "1p800GHz", archive.name, log=lambda _: None)


def test_extract_rejects_links(tmp_path):
    archive = tmp_path / "context.tar"
    with tarfile.open(archive, "w") as tar:
        member = tarfile.TarInfo("link.txt")
        member.type, member.linkname = tarfile.SYMTYPE, "../outside"
        tar.addfile(member)
    with pytest.raises(ValueError, match="link/special"):
        dl.extract_archives([archive], tmp_path / "out", "Haworth", "1p800GHz", archive.name, log=lambda _: None)


def test_lola_reuses_cache_and_downloads_only_missing(tmp_path, config, monkeypatch):
    from moonmesh import data as app_data
    cache = tmp_path / "app-cache"
    (cache / "terrain").mkdir(parents=True)
    monkeypatch.setattr(app_data, "DATA", cache)
    monkeypatch.setenv("MOONMESH_DATA_ROOT", str(tmp_path / "data"))
    for product in app_data.PRODUCTS:
        for suffix in (".LBL", ".IMG"):
            (cache / "terrain" / (product + suffix)).write_bytes(
                b'PRODUCT_VERSION_ID = "V2.0"\n' if suffix == ".LBL" else b"cached")
    missing = cache / "terrain/LDEM_75S_240M.LBL"
    missing.unlink()
    calls = []

    def app_download(url, path, size=None):
        calls.append((url, path, size))
        path.write_bytes(b"downloaded")

    monkeypatch.setattr(app_data, "download", app_download)
    worker = downloader(tmp_path, config, Session(None))
    dl.register_lola(worker)
    entries = json.loads(worker.manifest.read_text())["entries"]
    assert len(entries) == 4
    assert len(calls) == 1
    assert calls[0][1] == tmp_path / "data/raw/lola_pds/LDEM_75S_240M.LBL"
    assert not missing.exists()
    assert sum(Path(entry["local_path"]).is_relative_to(cache) for entry in entries) == 3
    assert [entry["version"] for entry in entries] == ["V2.0", "V2.0", None, None]
    assert not worker.session.calls


def write_raster(path, size=10, transform=None, crs="EPSG:3857"):
    with rasterio.open(path, "w", driver="GTiff", width=size, height=size, count=1,
                       dtype="int16", crs=crs, transform=transform or from_origin(0, 100, 10, 10),
                       nodata=-9999) as target:
        target.write(np.arange(size*size, dtype="int16").reshape(1, size, size))
        target.scales = (0.5,)
        target.update_tags(source="offline fixture")


def test_buffered_window_reprojects_geographic_bounds(tmp_path):
    source = tmp_path / "source.tif"
    reference = tmp_path / "reference.tif"
    write_raster(source, transform=from_origin(0, 10000, 1000, 1000))
    write_raster(reference, size=1, transform=from_origin(0.02, 0.05, 0.01, 0.01), crs="EPSG:4326")
    with rasterio.open(source) as src, rasterio.open(reference) as ref:
        window = dl.buffered_window(src, ref, 1000)
        assert window.flatten() == (1, 3, 4, 4)


def test_pgda90_crops_local_fixture_through_mocked_vsicurl(tmp_path, config, monkeypatch):
    source = tmp_path / "source.tif"
    reference = tmp_path / "reference.tif"
    write_raster(source)
    write_raster(reference, size=2, transform=from_origin(20, 80, 10, 10))
    monkeypatch.setenv("MOONMESH_DATA_ROOT", str(tmp_path / "data"))
    real_open = rasterio.open
    opened = []

    def fake_open(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(source if str(path).startswith("/vsicurl/") else path, *args, **kwargs)

    monkeypatch.setattr(rasterio, "open", fake_open)
    session = Session(1000, Response(status=206, headers={"Content-Range": "bytes 0-0/1000"}))
    worker = downloader(tmp_path, config, session)
    entry = dl.pgda90_window(worker, reference, 10)
    with real_open(entry["local_path"]) as result, real_open(source) as src:
        assert result.shape == (4, 4)
        np.testing.assert_array_equal(result.read(1), src.read(1)[1:5, 1:5])
        assert result.scales == (0.5,)
        assert result.nodata == -9999
        assert result.tags()["source"] == "offline fixture"
        assert result.transform == from_origin(10, 90, 10, 10)
    assert any(path.startswith("/vsicurl/https://pgda.gsfc.nasa.gov/") for path in opened)
    assert entry["verified"] == "unverified"
    assert entry["source_window"] == [1, 1, 4, 4]


@pytest.mark.parametrize("head_size", [3_000_000_000, None])
def test_pgda90_range_failure_never_reads_full_body(tmp_path, config, head_size):
    probe = Response(b"must not be consumed", headers={"Content-Length": "3000000000"})
    session = Session(head_size, probe)
    worker = downloader(tmp_path, config, session)
    logs = []
    worker.log = logs.append
    with pytest.raises(ValueError, match="Range probe"):
        dl.pgda90_window(worker, tmp_path / "not-opened.tif", 10000)
    assert not probe.consumed and probe.closed
    assert any("Full file size: 3000000000 bytes" in line for line in logs)
    assert any("No full-file fallback" in line for line in logs)
    assert not worker.manifest.exists()


def test_cli_help_and_subcommands():
    path = paths.repo_root() / "training/scripts/00_download.py"
    spec = importlib.util.spec_from_file_location("download_cli", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    assert cli.parser().parse_args(["lunarrf", "--parts", "context", "1", "--yes"]).yes
    assert cli.parser().parse_args(["pgda78", "--site", "malapert", "--files", "supplied.tif"]).files == ["supplied.tif"]


@pytest.mark.parametrize("part,name", [
    ("mosaics2", "lunarrf_atlas_mosaics_160m_part2_v2.0.tar"),
    ("candidates", "lunarrf_candidates_v2.0.tar"),
    ("validation", "lunarrf_validation_v2.0.tar"),
])
def test_new_parts_resume_verify_reuse_and_record(tmp_path, config, cli, monkeypatch, part, name):
    monkeypatch.setenv("MOONMESH_DATA_ROOT", str(tmp_path / "data"))
    root = paths.raw_dir("lunarrf")
    (root / "SHA256SUMS").write_text(f"{digest(b'abcdef')}  {name}\n")
    (root / (name + ".part")).write_bytes(b"abc")
    session = Session(6, Response(b"def", 206, {"Content-Range": "bytes 3-5/6"}))
    worker = downloader(tmp_path, config, session)
    args = cli.parser().parse_args(["lunarrf", "--parts", part, part])
    cli.run(args, worker)
    assert (root / name).read_bytes() == b"abcdef"
    assert [call[0] for call in session.calls] == ["HEAD", "GET"]
    assert session.calls[0][1] == config["lunarrf"]["base_url"] + name + "?download=1"
    assert session.calls[1][2]["headers"]["Range"] == "bytes=3-"
    entry = json.loads(worker.manifest.read_text())["entries"][0]
    assert entry["verified"] is True and entry["label_provenance"] == "simulated"
    assert entry["source_id"] == dl.file_source_id("lunarrf", root / name)
    cli.run(args, worker)
    assert json.loads(worker.manifest.read_text())["entries"] == [entry]
    assert session.calls[-1][0] == "HEAD"


@pytest.mark.parametrize("part", ["mosaics2", "candidates", "validation"])
def test_new_parts_keep_size_and_checksum_rules(tmp_path, config, cli, monkeypatch, part):
    monkeypatch.setenv("MOONMESH_DATA_ROOT", str(tmp_path / "data"))
    root = paths.raw_dir("lunarrf")
    name = config["lunarrf"]["archives"][part]
    args = cli.parser().parse_args(["lunarrf", "--parts", part])
    worker = downloader(tmp_path, config, Session(None))
    with pytest.raises(ValueError, match="lunarrf-meta first"):
        cli.run(args, worker)
    assert not worker.session.calls
    (root / "SHA256SUMS").write_text(f"{digest(b'abc')}  {name}\n")
    for size in [None, 2_000_000_001]:
        worker.session = Session(size)
        with pytest.raises(ValueError, match="--yes"):
            cli.run(args, worker)
        assert [call[0] for call in worker.session.calls] == ["HEAD"]
    worker.session = Session(3, Response(b"bad"))
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        cli.run(args, worker)
    assert not (root / name).exists() and not worker.manifest.exists()
    (root / "SHA256SUMS").write_text("")
    worker.session = Session(3)
    cli.run(args, worker)
    entry = json.loads(worker.manifest.read_text())["entries"][0]
    assert entry["verified"] == "unverified" and dl.UNVERIFIED in entry["notes"]


def test_mosaics_part1_not_offered(config, cli):
    assert "lunarrf_atlas_mosaics_160m_part1_v2.0.tar" not in config["lunarrf"]["archives"].values()
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["lunarrf", "--parts", "mosaics1"])


def test_extract_meta_all_downloaded_archives_and_figures(tmp_path, config, cli, monkeypatch):
    monkeypatch.setenv("MOONMESH_DATA_ROOT", str(tmp_path / "data"))
    root = paths.raw_dir("lunarrf")
    metadata = {f"nested/meta{suffix.upper()}": b"metadata" for suffix in dl.METADATA_SUFFIXES}
    members = {
        config["lunarrf"]["archives"]["mosaics2"]: {
            **metadata, "atlas_v2/radio_params.json": b"{}", "atlas_v2/manifest.json": b"{}",
            "atlas_v2/bundle/store.json": b"{}", "atlas_v2/bundle/stats_exact/disc_exact.csv": b"a,b"},
        config["lunarrf"]["archives"]["candidates"]: {
            "data/atlas_base/candidates.csv": b"base", "data/atlas_base/candidates_freeze.csv": b"freeze",
            "data/atlas_power_control/candidates.csv": b"power", "data/atlas_power_control/subset_ids.csv": b"ids",
            "data/atlas_v2/bundle/candidates_coverage.csv": b"coverage"},
        config["lunarrf"]["archives"]["validation"]: {
            "figures/validation/plot.PNG": b"png", "figures/validation/pe_reference/plot.pdf": b"pdf"},
        "lunarrf_sideruns_core_part1_v2.0.tar": {"data/run/radio_params.json": b"siderun"},
    }
    expected = {}
    for name, files in members.items():
        expected.update(files)
        make_tar(root / name, {**files, "excluded.tif": b"raster", "excluded.bin": b"binary"})
    make_tar(root / config["lunarrf"]["archives"]["context"], {"excluded.png": b"image", "excluded.pdf": b"pdf"})
    (root / "lunarrf_incomplete.tar.part").write_bytes(b"not a tar yet")
    before = {path: dl.sha256_file(path) for path in root.iterdir()}
    worker = downloader(tmp_path, config, Session(None))
    args = cli.parser().parse_args(["extract-meta"])
    result = cli.run(args, worker)
    output = paths.interim_dir() / "lunarrf/meta"
    assert {Path(path).relative_to(output).as_posix(): Path(path).read_bytes()
            for path in result["outputs"]} == expected
    assert result["label_provenance"] == "simulated"
    assert cli.run(args, worker) == result
    assert before == {path: dl.sha256_file(path) for path in root.iterdir()}
    assert not worker.session.calls


@pytest.mark.parametrize("name", ["../escape.json", "/escape.csv", "C:/escape.txt", "..\\escape.md", "dir/CON.xml"])
def test_extract_meta_rejects_unsafe_paths(tmp_path, name):
    archive = tmp_path / "candidates.tar"
    make_tar(archive, {"valid.json": b"{}", name: b"bad"})
    with pytest.raises(ValueError):
        dl.extract_metadata([archive], tmp_path / "out", "validation.tar", log=lambda _: None)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_extract_meta_rejects_special_members(tmp_path, kind):
    archive = tmp_path / "validation.tar"
    with tarfile.open(archive, "w") as tar:
        member = tarfile.TarInfo("figures/link.png")
        member.type, member.linkname = kind, "../outside"
        tar.addfile(member)
    with pytest.raises(ValueError, match="link/special"):
        dl.extract_metadata([archive], tmp_path / "out", archive.name, log=lambda _: None)


def test_extract_meta_collision_preserves_existing_file(tmp_path):
    archives = [tmp_path / "one.tar", tmp_path / "two.tar"]
    for archive, payload in zip(archives, [b"one", b"two"]):
        make_tar(archive, {"nested/radio_params.json": payload})
    with pytest.raises(ValueError, match="collision"):
        dl.extract_metadata(archives, tmp_path / "out", "validation.tar", log=lambda _: None)
    assert (tmp_path / "out/nested/radio_params.json").read_bytes() == b"one"


def test_sites_from_manifest_sections_without_archives(tmp_path, config, cli, monkeypatch):
    monkeypatch.setenv("MOONMESH_DATA_ROOT", str(tmp_path / "data"))
    part1 = config["lunarrf"]["archives"]["1"]
    part2 = config["lunarrf"]["archives"]["2"]
    siderun = "lunarrf_sideruns_regions_20m_part1_v2.0.tar"
    manifest = paths.raw_dir("lunarrf") / "MANIFEST.txt"
    manifest.write_text(f"""{part1} 398.4 MB 232 files
Contents of each archive:
--- {config['lunarrf']['archives']['context']}
  atlas_v2/context/regions/Haworth__dem.tif
--- {part1}
  atlas_v2/bundle/regions/Haworth__best_prx_1p800GHz.tif
  atlas_v2/bundle/regions/Haworth__los_count.tif
  atlas_v2/bundle/regions/Malapert_Massif__best_id_1p800GHz.tif
  atlas_v2/bundle/regions/Connecting_Ridge__los_count.tif
  atlas_v2/bundle/regions/Mons_Mouton_Plateau__los_count.tif
  atlas_v2/bundle/regions/Mons_Mouton__los_count.tif
--- {part2}
  atlas_v2/bundle/regions/Malapert_Massif__los_count.tif
  atlas_v2/bundle/regions/Nobile_Rim_1__los_count.tif
--- {siderun}
  data/run1/regions/Haworth__los_count.tif
  data/run2/regions/Haworth__los_count.tif
--- provenance ---
  ignored__footer.txt
""")
    expected = {
        "Haworth": {part1: 2, siderun: 2}, "Malapert_Massif": {part1: 1, part2: 1},
        "Nobile_Rim_1": {part2: 1}, "Connecting_Ridge": {part1: 1},
        "Mons_Mouton_Plateau": {part1: 1}, "Mons_Mouton": {part1: 1},
    }
    worker = downloader(tmp_path, config, Session(None))
    logs = []
    worker.log = logs.append
    result = cli.run(cli.parser().parse_args(["sites"]), worker)
    assert result == {"sites": expected, "label_provenance": "simulated"}
    assert "Haworth:" in logs and f"  {part1}: 2 files" in logs
    assert not worker.session.calls


def test_sites_missing_or_unrecognized_manifest(tmp_path):
    manifest = tmp_path / "MANIFEST.txt"
    with pytest.raises(ValueError, match="lunarrf-meta first"):
        dl.lunarrf_sites(manifest)
    manifest.write_text("No archive sections")
    with pytest.raises(ValueError, match="No region site files"):
        dl.lunarrf_sites(manifest)


def test_file_source_ids_distinguish_files_and_preserve_versions(tmp_path, config):
    names = {
        "lunarrf": {"lunarrf_atlas_context_v2.0.tar": "lunarrf_context_v2.0",
                    "lunarrf_atlas_regions_20m_part1_v2.0.tar": "lunarrf_regions_20m_part1_v2.0",
                    "MANIFEST.txt": "lunarrf_manifest_txt", "SHA256SUMS": "lunarrf_sha256sums"},
        "pgda78": {"Haworth_final_adj_5mpp_surf.tif": "pgda78_haworth_5m",
                   "Haworth_slope.tif": "pgda78_haworth_slope_tif"},
        "lola_pds": {"LDEM_85S_20M.IMG": "lola_pds_ldem_85s_20m_img",
                     "LDEM_85S_20M.LBL": "lola_pds_ldem_85s_20m_lbl"},
        "pgda90": {"window_a.tif": "pgda90_window_a_tif", "window_b.tif": "pgda90_window_b_tif"},
    }
    worker = downloader(tmp_path, config, Session(None))
    for source, files in names.items():
        for name, expected in files.items():
            path = tmp_path / name
            path.write_bytes(b"same content still distinct files")
            assert worker.record(path, source, "url")["source_id"] == expected
    entries = json.loads(worker.manifest.read_text())["entries"]
    assert len({entry["source_id"] for entry in entries}) == sum(map(len, names.values()))


def test_manifest_renamed_id_upserts_legacy_path_and_rejects_duplicate_id(tmp_path, config):
    worker = downloader(tmp_path, config, Session(None))
    target = tmp_path / "MANIFEST.txt"
    target.write_bytes(b"manifest")
    entry = worker.record(target, "lunarrf", "url", downloaded_utc="known-time")
    legacy = dict(entry, source_id="lunarrf")
    dl.atomic_json(worker.manifest, {"schema_version": 1, "entries": [legacy]})
    assert worker.record(target, "lunarrf", "url") == entry
    before = worker.manifest.read_bytes()
    with pytest.raises(ValueError, match="already belongs"):
        dl.update_manifest(worker.manifest, dict(entry, local_path=str(tmp_path / "another.txt")))
    assert worker.manifest.read_bytes() == before


def test_checked_in_manifest_has_unique_per_file_ids():
    manifest = json.loads((paths.repo_root() / "training/data_manifest.json").read_text())
    entries = manifest["entries"]
    assert len({entry["source_id"] for entry in entries}) == len(entries)
    assert not {entry["source_id"] for entry in entries} & {"lunarrf", "pgda78", "lola_pds", "pgda90"}
