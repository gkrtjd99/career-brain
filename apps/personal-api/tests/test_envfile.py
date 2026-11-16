from personal_api.envfile import load_env_file, parse_env


def test_parse_env_formats():
    text = "# comment\n\nA=1\nexport B = two \nC=\"quoted value\"\nD='x'\nnot a line\n1BAD=x\nE=\n"
    assert parse_env(text) == {"A": "1", "B": "two", "C": "quoted value", "D": "x", "E": ""}


def test_existing_env_wins(tmp_path):
    f = tmp_path / ".env"
    f.write_text("KEEP=file\nNEW=file\n")
    env = {"KEEP": "shell"}
    assert load_env_file(f, env) == ["NEW"]
    assert env == {"KEEP": "shell", "NEW": "file"}


def test_missing_file_is_noop(tmp_path):
    env = {}
    assert load_env_file(tmp_path / "nope", env) == []
    assert env == {}
