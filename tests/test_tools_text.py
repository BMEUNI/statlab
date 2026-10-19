"""Chunk 82a: text vectorizer tools. Learned on train, replayed on held-out rows; the lexicon's
train output is out-of-fold."""

import numpy as np
import pandas as pd
import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import load_dataframe, save_dataframe
from statlab_tools.tool import ToolDataError, ToolParamError
from statlab_tools.tools.encoding import apply_text_vectorizer, fit_text_vectorizer


def _dict(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _list(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def _save(paths: RunPaths, frame: pd.DataFrame) -> ArtifactRef:
    ref = new_artifact_ref()
    save_dataframe(paths.artifact(ref, "dataframe"), frame, ref=ref, label="raw")
    return ref


def _loaded(paths: RunPaths, res: ToolSuccess, index: int = 0) -> pd.DataFrame:
    return load_dataframe(paths.artifact(res.artifacts[index].ref, "dataframe"))


REVIEWS = [
    "great app love it",
    "great app",
    "love it great",
    "bad crash",
    "bad app crash",
    "crash bad",
]
TRAIN = pd.DataFrame({"review": REVIEWS, "n": [1, 2, 3, 4, 5, 6], "y": [1, 1, 1, 0, 0, 0]})


def test_tfidf_learns_train_vocabulary_and_replays_on_valid(run_paths: RunPaths) -> None:
    fit = fit_text_vectorizer(
        run_paths, _save(run_paths, TRAIN), target="y", columns=("review",), method="tfidf", seed=0
    )
    train_out = _loaded(run_paths, fit, 1)
    assert "review" not in train_out.columns
    assert train_out["n"].tolist() == [1, 2, 3, 4, 5, 6]
    assert train_out["y"].tolist() == [1, 1, 1, 0, 0, 0]
    assert {"review__tfidf_great", "review__tfidf_great+app", "review__tfidf_crash"} <= set(
        train_out.columns
    )
    assert fit.summary["method"] == "tfidf"

    valid = pd.DataFrame(
        {"review": ["great love", "new words only", None], "n": [7, 8, 9], "y": [1, 0, 1]},
        index=[10, 11, 12],
    )
    out = _loaded(
        run_paths, apply_text_vectorizer(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    )
    assert list(out.columns) == list(train_out.columns)
    text = [c for c in out.columns if c.startswith("review__")]
    assert out["review__tfidf_great"].to_numpy(dtype="float64")[0] > 0
    assert (out.loc[11, text] == 0).all()
    assert (out.loc[12, text] == 0).all()


def test_hashed_chars_drops_constant_buckets_and_is_deterministic(run_paths: RunPaths) -> None:
    fit = fit_text_vectorizer(
        run_paths,
        _save(run_paths, TRAIN),
        target="y",
        columns=("review",),
        method="hashed_chars",
        seed=0,
    )
    train_out = _loaded(run_paths, fit, 1)
    hashed = [c for c in train_out.columns if c.startswith("review__hash_")]
    assert 0 < len(hashed) < 256
    assert _dict(fit.summary["columns"]) == {"review": len(hashed)}
    assert (train_out[hashed].max() - train_out[hashed].min() > 0).all()

    valid = pd.DataFrame({"review": ["bad crash"], "n": [9], "y": [0]}, index=[20])
    out = _loaded(
        run_paths, apply_text_vectorizer(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    )
    assert np.allclose(
        out.loc[20, hashed].to_numpy(dtype="float64"),
        train_out.loc[3, hashed].to_numpy(dtype="float64"),
    )


def test_lexicon_learns_class_keywords_out_of_fold(run_paths: RunPaths) -> None:
    reviews = []
    for i in range(20):
        text = f"good great love w{i}" if i < 10 else f"bad awful crash w{i}"
        if i < 2:
            text += " zeta"
        reviews.append(text)
    frame = pd.DataFrame({"review": reviews, "y": [1] * 10 + [0] * 10})
    fit = fit_text_vectorizer(
        run_paths,
        _save(run_paths, frame),
        target="y",
        columns=("review",),
        method="lexicon",
        seed=0,
    )
    kw = _dict(_dict(fit.summary["keywords"])["review"])
    assert {"good", "great", "love", "zeta"} <= set(_list(kw["1"]))
    assert {"bad", "awful", "crash"} <= set(_list(kw["0"]))
    assert "w3" not in _list(kw["1"])

    train_out = _loaded(run_paths, fit, 1)
    assert [c for c in train_out.columns if c != "y"] == ["review__lex_0", "review__lex_1"]
    # out-of-fold: zeta is in only one other row, so the fold holding row 0 out never learns it
    assert train_out.loc[0, "review__lex_1"] == 3.0

    valid = pd.DataFrame({"review": ["good great love zeta"], "y": [1]}, index=[100])
    out = _loaded(
        run_paths, apply_text_vectorizer(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    )
    assert out.loc[100, "review__lex_1"] == 4.0
    assert out.loc[100, "review__lex_0"] == 0.0


def test_lexicon_refusals(run_paths: RunPaths) -> None:
    continuous = pd.DataFrame(
        {"review": [f"word{i % 3} text" for i in range(30)], "y": [i * 0.5 for i in range(30)]}
    )
    with pytest.raises(ToolParamError, match="class target"):
        fit_text_vectorizer(
            run_paths,
            _save(run_paths, continuous),
            target="y",
            columns=("review",),
            method="lexicon",
            seed=0,
        )
    with pytest.raises(ToolParamError):
        fit_text_vectorizer(
            run_paths,
            _save(run_paths, TRAIN.drop(columns=["y"])),
            target="y",
            columns=("review",),
            method="lexicon",
            seed=0,
        )
    with pytest.raises(ToolDataError, match="cross-fitting folds"):
        fit_text_vectorizer(
            run_paths,
            _save(run_paths, TRAIN),
            target="y",
            columns=("review",),
            method="lexicon",
            seed=0,
        )


def test_unusable_text_is_a_data_error(run_paths: RunPaths) -> None:
    no_repeat = pd.DataFrame({"review": ["alpha", "beta", "gamma"], "y": [0, 1, 0]})
    with pytest.raises(ToolDataError, match="at least 2 train rows"):
        fit_text_vectorizer(
            run_paths,
            _save(run_paths, no_repeat),
            target="y",
            columns=("review",),
            method="tfidf",
            seed=0,
        )
    constant = pd.DataFrame({"review": ["same text"] * 4, "y": [0, 1, 0, 1]})
    with pytest.raises(ToolDataError, match="varies across train rows"):
        fit_text_vectorizer(
            run_paths,
            _save(run_paths, constant),
            target="y",
            columns=("review",),
            method="hashed_chars",
            seed=0,
        )


def test_apply_refuses_train_rows_and_names_must_not_collide(run_paths: RunPaths) -> None:
    fit = fit_text_vectorizer(
        run_paths, _save(run_paths, TRAIN), target="y", columns=("review",), method="tfidf", seed=0
    )
    with pytest.raises(ToolParamError, match="share a label"):
        apply_text_vectorizer(run_paths, fit.artifacts[0].ref, _save(run_paths, TRAIN))

    clash = TRAIN.assign(review__tfidf_great=0.0)
    with pytest.raises(ToolParamError, match="collide"):
        fit_text_vectorizer(
            run_paths,
            _save(run_paths, clash),
            target="y",
            columns=("review",),
            method="tfidf",
            seed=0,
        )
