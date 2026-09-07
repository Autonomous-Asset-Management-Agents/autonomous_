"""#2930 — Korpus-Verteilung: die Prüfungen müssen halten, sonst ist es eine Kopie.

Kern der Aussage: Ein verteilter Korpus ist nur dann derselbe Korpus, wenn BEIDE Hashes
stimmen — der des Archivs (Übertragung) und der des Korpus-Manifests (Identität). Jede
Abweichung muss ABBRECHEN, nicht warnen.
"""

from __future__ import annotations

import json
import tarfile

import pytest

from research.corpus_dist import pack, unpack, verify


def _make_corpus(tmp_path, total_hash="abc123def456", files=("A.parquet", "B.parquet")):
    d = tmp_path / "sim_corpus_test"
    d.mkdir()
    for i, name in enumerate(files):
        (d / name).write_bytes(b"bars-" + bytes([i]))
    (d / "manifest.json").write_text(
        json.dumps({"total_hash": total_hash, "symbols": {}}), encoding="utf-8"
    )
    return d


def test_pack_writes_archive_and_manifest_with_content_hash(tmp_path):
    corpus = _make_corpus(tmp_path)
    r = pack(corpus, tmp_path / "out")
    assert r["total_hash"] == "abc123def456"
    assert r["suggested_tag"] == "corpus-abc123de"  # Tag traegt den Inhalts-Hash
    assert len(r["archive_sha256"]) == 64
    assert r["n_files"] == 3  # 2 Parquet + manifest.json


def test_roundtrip_restores_the_same_corpus(tmp_path):
    corpus = _make_corpus(tmp_path)
    r = pack(corpus, tmp_path / "out")
    got = unpack(r["archive"], r["manifest"], tmp_path / "restored")
    assert got["total_hash"] == "abc123def456"
    assert got["n_files"] == 3
    assert (tmp_path / "restored" / "A.parquet").read_bytes() == b"bars-\x00"


def test_corrupt_archive_is_refused_not_warned(tmp_path):
    corpus = _make_corpus(tmp_path)
    r = pack(corpus, tmp_path / "out")
    with open(r["archive"], "ab") as fh:
        fh.write(b"kaputt")
    assert verify(r["archive"], r["manifest"])["ok"] is False
    with pytest.raises(ValueError, match="SHA256 weicht ab"):
        unpack(r["archive"], r["manifest"], tmp_path / "restored")


def test_wrong_corpus_behind_a_valid_archive_is_refused(tmp_path):
    """Der eigentliche Schutz: das Archiv ist unversehrt, enthaelt aber einen ANDEREN Korpus."""
    corpus = _make_corpus(tmp_path)
    r = pack(corpus, tmp_path / "out")
    # Manifest im Archiv gegen ein fremdes austauschen, Archiv-SHA neu setzen
    other = tmp_path / "other"
    other.mkdir()
    (other / "manifest.json").write_text(
        json.dumps({"total_hash": "999999999999"}), encoding="utf-8"
    )
    (other / "A.parquet").write_bytes(b"bars-\x00")
    fake = tmp_path / "out" / "fake.tar.gz"
    with tarfile.open(fake, "w:gz") as tf:
        for f in sorted(other.iterdir()):
            tf.add(f, arcname=f.name)
    m = json.loads(
        (tmp_path / "out" / "sim_corpus_test.manifest.json").read_text(encoding="utf-8")
    )
    from research.corpus_dist import sha256_file

    m["archive_sha256"] = sha256_file(fake)
    mp = tmp_path / "out" / "fake.manifest.json"
    mp.write_text(json.dumps(m), encoding="utf-8")

    with pytest.raises(ValueError, match="nicht der behauptete Korpus"):
        unpack(fake, mp, tmp_path / "restored2")


def test_path_escape_in_archive_is_refused(tmp_path):
    """tar-slip: ein Eintrag mit Pfadanteil darf nicht entpackt werden."""
    corpus = _make_corpus(tmp_path)
    r = pack(corpus, tmp_path / "out")
    evil = tmp_path / "out" / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tf:
        p = tmp_path / "manifest.json"
        p.write_text(json.dumps({"total_hash": "abc123def456"}), encoding="utf-8")
        tf.add(p, arcname="manifest.json")
        bad = tmp_path / "x.parquet"
        bad.write_bytes(b"x")
        tf.add(bad, arcname="../escaped.parquet")
    from research.corpus_dist import sha256_file

    m = json.loads(
        (tmp_path / "out" / "sim_corpus_test.manifest.json").read_text(encoding="utf-8")
    )
    m["archive_sha256"] = sha256_file(evil)
    mp = tmp_path / "out" / "evil.manifest.json"
    mp.write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(ValueError, match="unerwarteter Archiv-Eintrag"):
        unpack(evil, mp, tmp_path / "restored3")


def test_missing_corpus_manifest_is_a_clear_error(tmp_path):
    d = tmp_path / "no_manifest"
    d.mkdir()
    (d / "A.parquet").write_bytes(b"x")
    with pytest.raises(FileNotFoundError, match="manifest.json fehlt|fehlt"):
        pack(d, tmp_path / "out")
