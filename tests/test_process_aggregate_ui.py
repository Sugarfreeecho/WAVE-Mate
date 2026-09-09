from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_collapsed_process_brief_does_not_render_status_rows():
    rendering = (ROOT / "frontend/src/app/modules/message-rendering.js").read_text(
        encoding="utf-8"
    )
    brief_renderer = rendering.split("function updateProcessBrief(agg)", 1)[1].split(
        "function syncProcessAggregateHeightUi", 1
    )[0]

    assert "body.querySelector('.feed-item.feed--st" not in brief_renderer
    assert ":not(.feed--st)" in brief_renderer
    assert "tAny || '本段过程已折叠'" in brief_renderer


def test_terminal_run_collapses_process_before_sealing_context():
    source = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(
        encoding="utf-8"
    )
    end_run = source.split("function endRunForClient", 1)[1].split(
        "async function readSseChunkWithIdleTimeout", 1
    )[0]

    collapse = "terminalAggregate.classList.add('is-collapsed');"
    assert collapse in end_run
    assert end_run.index(collapse) < end_run.index("sealProcessGroup(ctx);")
