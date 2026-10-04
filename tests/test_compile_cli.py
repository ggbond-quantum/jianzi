from test_compile import make_fixture_db

from jianzipu.compile.cli import main


def test_cli_compiles_font(tmp_path):
    db = make_fixture_db(tmp_path / "fixture.sqlite")
    main(["--db", str(db), "--out-dir", str(tmp_path), "--formats", "ttf"])
    assert (tmp_path / "JianziPu-测试作者.ttf").exists()
    assert (tmp_path / "char_to_unicode-测试作者.json").exists()