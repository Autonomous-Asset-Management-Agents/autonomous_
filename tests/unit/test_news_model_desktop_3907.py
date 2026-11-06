# tests/unit/test_news_model_desktop_3907.py
# #3907 (Epic #3702) — die Desktop-App bewertet Schlagzeilen offline mit einem
# lizenzklaren Finanz-Sprachmodell (DistilRoBERTa, Apache-2.0) aus einem
# mitgelieferten Verzeichnis statt mit der VADER-Wortliste.
#
#   TestModellKennung   — Kennung "fin-distilroberta", Default in settings.py
#   TestVerzeichnisLader — NEWS_SENTIMENT_MODEL_DIR ⇒ Laden aus dem Verzeichnis,
#                          local_files_only=True; fehlt es ⇒ Wortliste + WARNING
#   TestLabels          — _finbert_signed liest die Label-Reihenfolge beider Modelle
#   TestDiagnose        — /engine-diagnostics meldet news_sentiment.backend
#   TestAgentVerdrahtung — der Agent reicht das Verzeichnis aus der Konfiguration durch
#
# Kein echtes Modell, kein Netz: `transformers` wird je Test durch eine
# gefälschte Modul-Attrappe ersetzt (monkeypatch.setitem, nicht global).

from __future__ import annotations

import logging
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.vc1

_DISTILROBERTA_ID = "mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis"


def _fake_transformers(monkeypatch, label_scores=None):
    """Attrappe für `transformers`: zeichnet die from_pretrained-Aufrufe auf."""
    calls = []
    scores = label_scores or [
        {"label": "negative", "score": 0.05},
        {"label": "neutral", "score": 0.15},
        {"label": "positive", "score": 0.80},
    ]

    class _Auto:
        def __init__(self, kind):
            self.kind = kind

        def from_pretrained(self, name, **kwargs):
            calls.append((self.kind, name, kwargs))
            return SimpleNamespace(kind=self.kind, name=name)

    def pipeline(task, model=None, tokenizer=None, **kwargs):
        return lambda texts: [list(scores) for _ in texts]

    mod = ModuleType("transformers")
    mod.AutoTokenizer = _Auto("tokenizer")
    mod.AutoModelForSequenceClassification = _Auto("model")
    mod.pipeline = pipeline
    monkeypatch.setitem(sys.modules, "transformers", mod)
    return calls


@pytest.fixture(autouse=True)
def _frische_backends():
    from core.nlp import news_sentiment as ns

    ns._reset_backends_for_tests()
    yield
    ns._reset_backends_for_tests()


def _modell_verzeichnis(tmp_path):
    d = tmp_path / "models" / "news_sentiment"
    d.mkdir(parents=True)
    (d / "config.json").write_text("{}", encoding="utf-8")
    return d


_TITEL = [{"title": "Company X beats earnings expectations", "source": "r"}]


class TestModellKennung:
    def test_fin_distilroberta_ist_eine_bekannte_kennung(self):
        from core.nlp import news_sentiment as ns

        assert ns._FINBERT_MODEL_IDS["fin-distilroberta"] == _DISTILROBERTA_ID

    def test_default_kennung_und_verzeichnis_in_beiden_editionen(self, monkeypatch):
        monkeypatch.delenv("NEWS_SENTIMENT_MODEL", raising=False)
        monkeypatch.delenv("NEWS_SENTIMENT_MODEL_DIR", raising=False)
        import settings

        fields = settings.RuntimeConfigState.model_fields
        assert fields["NEWS_SENTIMENT_MODEL"].default == "fin-distilroberta"
        assert fields["NEWS_SENTIMENT_MODEL_DIR"].default == ""


class TestVerzeichnisLader:
    def test_laedt_aus_dem_verzeichnis_nur_lokal(self, monkeypatch, tmp_path):
        from core.nlp import news_sentiment as ns

        calls = _fake_transformers(monkeypatch)
        d = _modell_verzeichnis(tmp_path)
        res = ns.score_headlines(_TITEL, model="fin-distilroberta", model_dir=str(d))
        assert res is not None
        assert res.backend == "fin-distilroberta"
        assert res.score > 0.5
        assert {c[1] for c in calls} == {str(d)}, "nur aus dem Verzeichnis laden"
        assert all(c[2].get("local_files_only") is True for c in calls)

    def test_ohne_verzeichnis_bleibt_die_kennung_lokal(self, monkeypatch):
        from core.nlp import news_sentiment as ns

        calls = _fake_transformers(monkeypatch)
        res = ns.score_headlines(_TITEL, model="fin-distilroberta")
        assert res is not None and res.backend == "fin-distilroberta"
        assert {c[1] for c in calls} == {_DISTILROBERTA_ID}
        assert all(c[2].get("local_files_only") is True for c in calls)

    def test_paralleler_erstaufruf_laedt_das_modell_nur_einmal(self, monkeypatch):
        """Jetzt lädt das Modell wirklich (~10–30 s, ~750 MB): parallele Symbole
        dürfen es nicht je einmal laden."""
        import threading

        from core.nlp import news_sentiment as ns

        loads = []
        loader_entered = threading.Event()
        loader_release = threading.Event()

        def slow_loader(model, model_dir=""):
            loads.append(model)
            loader_entered.set()
            assert loader_release.wait(timeout=5.0), "Timeout beim Warten auf Freigabe"
            return lambda texts: [[{"label": "positive", "score": 0.9}] for _ in texts]

        monkeypatch.setattr(ns, "_load_finbert_pipeline", slow_loader)
        threads = [
            threading.Thread(
                target=ns.score_headlines, args=(_TITEL, "fin-distilroberta")
            )
            for _ in range(4)
        ]
        for t in threads:
            t.start()
        assert loader_entered.wait(timeout=5.0), "slow_loader wurde nicht aufgerufen"
        loader_release.set()
        for t in threads:
            t.join()
        assert len(loads) == 1

    def test_inferenz_laeuft_nicht_parallel(self, monkeypatch):
        """Schnelle HF-Tokenizer sind nicht threadsicher ("Already borrowed"):
        Inferenz aus parallelen Symbol-Threads wird nacheinander ausgeführt."""
        import threading

        from core.nlp import news_sentiment as ns

        state = {"inside": 0, "max": 0}
        guard = threading.Lock()
        pipe_entered = threading.Event()
        pipe_release = threading.Event()

        def pipe(texts):
            with guard:
                state["inside"] += 1
                state["max"] = max(state["max"], state["inside"])
                assert state["inside"] == 1, "Inferenz darf nicht parallel laufen"
            pipe_entered.set()
            assert pipe_release.wait(timeout=5.0), "Timeout beim Warten auf Freigabe"
            with guard:
                state["inside"] -= 1
            return [[{"label": "positive", "score": 0.9}] for _ in texts]

        monkeypatch.setattr(ns, "_load_finbert_pipeline", lambda model: pipe)
        threads = [
            threading.Thread(
                target=ns.score_headlines, args=(_TITEL, "fin-distilroberta")
            )
            for _ in range(4)
        ]
        for t in threads:
            t.start()
        assert pipe_entered.wait(timeout=5.0), "pipe wurde nicht aufgerufen"
        pipe_release.set()
        for t in threads:
            t.join()
        assert state["max"] == 1

    def test_fehlendes_verzeichnis_faellt_auf_die_wortliste(
        self, monkeypatch, tmp_path, caplog
    ):
        from core.nlp import news_sentiment as ns

        calls = _fake_transformers(monkeypatch)
        fehlt = tmp_path / "gibt-es-nicht"
        with caplog.at_level(logging.WARNING):
            r1 = ns.score_headlines(_TITEL, "fin-distilroberta", model_dir=str(fehlt))
            r2 = ns.score_headlines(_TITEL, "fin-distilroberta", model_dir=str(fehlt))
        assert r1 is not None and r1.backend == "VADER-Lexikon"
        assert r2 is not None and r2.backend == "VADER-Lexikon"
        assert calls == [], "ohne Verzeichnis darf nichts geladen werden"
        warnungen = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "gibt-es-nicht" in r.getMessage()
        ]
        assert len(warnungen) == 1, "genau eine WARNING mit Grund (Pfad)"
        assert ns.backend_status()["backend"] == "vader"


class TestLabels:
    @pytest.mark.parametrize(
        "raw",
        [
            # fin-distilroberta: id2label negative, neutral, positive
            [
                {"label": "negative", "score": 0.1},
                {"label": "neutral", "score": 0.2},
                {"label": "positive", "score": 0.7},
            ],
            # ProsusAI/finbert: id2label positive, negative, neutral
            [
                {"label": "positive", "score": 0.7},
                {"label": "negative", "score": 0.1},
                {"label": "neutral", "score": 0.2},
            ],
        ],
    )
    def test_label_reihenfolge_beider_modelle(self, raw):
        from core.nlp import news_sentiment as ns

        assert ns._finbert_signed(raw) == pytest.approx(0.6)
        assert ns._finbert_signed(list(reversed(raw))) == pytest.approx(0.6)


class TestDiagnose:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from core.auth import require_engine_key
        from core.engine.api_routes import app

        app.dependency_overrides[require_engine_key] = lambda: None
        yield TestClient(app)
        app.dependency_overrides.clear()

    def test_diagnose_meldet_das_modell(self, client, monkeypatch, tmp_path):
        from core.nlp import news_sentiment as ns

        _fake_transformers(monkeypatch)
        d = _modell_verzeichnis(tmp_path)
        ns.score_headlines(_TITEL, model="fin-distilroberta", model_dir=str(d))
        body = client.get("/engine-diagnostics").json()
        assert body["news_sentiment"]["backend"] == "fin-distilroberta"

    def test_diagnose_meldet_den_rueckfall(self, client, monkeypatch, tmp_path):
        from core.nlp import news_sentiment as ns

        _fake_transformers(monkeypatch)
        ns.score_headlines(
            _TITEL, model="fin-distilroberta", model_dir=str(tmp_path / "fehlt")
        )
        sub = client.get("/engine-diagnostics").json()["news_sentiment"]
        assert sub["backend"] == "vader"
        assert sub["fallback_reason"]


class TestAgentVerdrahtung:
    @pytest.mark.anyio
    async def test_agent_reicht_das_verzeichnis_durch(self, monkeypatch):
        import config
        import core.nlp.news_sentiment as ns
        from core.round_table import agents as agents_mod
        from core.round_table.agents import NewsSentimentAgent

        cfg = SimpleNamespace(
            NEWS_SENTIMENT_NLP_ENABLED=True,
            NEWS_SENTIMENT_MAX_HEADLINES=8,
            NEWS_SENTIMENT_MODEL="fin-distilroberta",
            NEWS_SENTIMENT_MODEL_DIR="C:/x/python/models/news_sentiment",
        )
        monkeypatch.setattr(config, "get_config", lambda: cfg)
        monkeypatch.setattr(agents_mod, "get_config", lambda: cfg, raising=False)
        agents_mod._LOCAL_SENTIMENT_CACHE.clear()
        scorer = MagicMock(
            return_value=ns.SentimentResult(
                score=0.7, details=[], backend="fin-distilroberta"
            )
        )
        monkeypatch.setattr(ns, "score_headlines", scorer)
        state = {
            "symbol": "AAPL",
            "news_headlines": [{"title": "Company X beats", "source": "r"}],
        }
        result = await NewsSentimentAgent().vote(state)
        assert "fin-distilroberta" in result.reasoning
        assert scorer.call_args.kwargs.get("model_dir") == (
            "C:/x/python/models/news_sentiment"
        )
