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
    make_tar(context, {"meta/run.JSON": b"{}", "unrelated.tif": b"no"})
    make_tar(region, {"maps/Haworth__best_prx_1p800GHz.tif": b"yes", "Haworth__los_count.tif": b"count",
                      "Haworth__best_prx_2p580GHz.tif": b"no", "Nobile__best_prx_1p800GHz.tif": b"no",
                      "regional.txt": b"no"})
    before = [dl.sha256_file(path) for path in (context, region)]
    output = tmp_path / "out"
    listing = []

    def log(message):
        if not message.startswith("Extracted"):
            assert not output.exists(), "All listings must precede extraction"
        listing.append(message)

    extracted = dl.extract_archives([context, region], output, "Haworth", "1p800GHz", context.name, log=log)
    assert {Path(path).name for path in extracted} == {"run.JSON", "Haworth__best_prx_1p800GHz.tif", "Haworth__los_count.tif"}
    assert len(listing) == 8
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
