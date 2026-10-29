"""The writer comes from hs_uploader, not sigmond's bundled copy."""
import sys

from codar_sounder.core.daemon import SounderDaemon
from tests.test_lifetime import _bare_config, _radiod_block


def test_writer_comes_from_hs_uploader_without_sigmond(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "sigmond", None)
    monkeypatch.setitem(sys.modules, "sigmond.hamsci_sink", None)
    monkeypatch.setenv("SIGMOND_SQLITE_PATH", str(tmp_path / "sink.db"))
    cfg = _bare_config()
    cfg["paths"]["output_dir"] = str(tmp_path / "out")
    writer = SounderDaemon(cfg, _radiod_block()).ch_writer
    assert writer is not None
    assert type(writer).__module__ == "hs_uploader.sink.writer"
    # is_noop may be an attribute or a method depending on the Writer.
    noop = writer.is_noop
    assert (noop() if callable(noop) else noop) is False

