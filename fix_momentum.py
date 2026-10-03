import pathlib

p = pathlib.Path("tests/unit/test_momentum_score_smoothing.py")
t = p.read_text(encoding="utf-8")
t = t.replace('["config.py", "config.oss.py"]', '["settings.py"]')
p.write_text(t, encoding="utf-8")
