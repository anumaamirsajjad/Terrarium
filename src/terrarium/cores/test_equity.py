"""Equity distribution, on fields with a known answer.

Every case here is constructed so the correct output is arithmetic rather than judgement:
uniform cooling must split evenly, cooling aimed at the crowded end must concentrate, and
a plan that warms the poor must not be able to hide it behind a good headline.

Nothing here touches the network or the disk.
"""

from __future__ import annotations

import numpy as np
import pytest

from terrarium.cores.equity import CONCENTRATION_THRESHOLD, benefit_distribution

SHAPE = (40, 40)


def _population(gradient: bool = True) -> np.ndarray:
    """Residents per cell, rising left to right so density has a clean ordering."""
    if not gradient:
        return np.full(SHAPE, 50.0)
    columns = np.linspace(1.0, 200.0, SHAPE[1])
    return np.repeat(columns[None, :], SHAPE[0], axis=0)


def test_uniform_cooling_is_shared_evenly() -> None:
    """The calibration case: same cooling everywhere means 10 % each, by construction.

    If this drifts, the deciles are being cut by cells rather than by people and every
    other number in the panel is uninterpretable.
    """
    delta = np.full(SHAPE, -1.0)

    result = benefit_distribution(delta, _population())

    for decile in result.deciles:
        assert decile.share == pytest.approx(0.10, abs=0.01), decile
    assert not result.concentrated
    assert result.top_three_share == pytest.approx(0.30, abs=0.03)


def test_each_decile_holds_a_tenth_of_the_people() -> None:
    result = benefit_distribution(np.full(SHAPE, -1.0), _population())

    tenth = result.population_covered / 10
    for decile in result.deciles:
        assert decile.people == pytest.approx(tenth, rel=0.05), decile


def test_cooling_only_the_crowded_end_is_flagged_as_concentrated() -> None:
    """The unflattering answer the panel exists to produce."""
    delta = np.zeros(SHAPE)
    delta[:, -8:] = -3.0  # cool only the densest columns

    result = benefit_distribution(delta, _population())

    assert result.concentrated
    assert result.top_three_share > CONCENTRATION_THRESHOLD
    # A band of uniformly-cooled deciles ties on share, so naming a single winner would
    # be asserting a tiebreak. What is determinate is which end the benefit sits at.
    assert result.best_served.decile >= 8
    assert result.deciles[-1].share > 0.2, "the densest decile must be well served"
    assert result.worst_served.share == pytest.approx(0.0, abs=1e-9)
    assert result.deciles[0].share == pytest.approx(0.0, abs=1e-9)


def test_cooling_only_the_sparse_end_concentrates_at_the_other_extreme() -> None:
    """Same skew, opposite direction - the flag must not be hardcoded to one end."""
    delta = np.zeros(SHAPE)
    delta[:, :8] = -3.0

    result = benefit_distribution(delta, _population())

    assert result.concentrated
    assert result.best_served.decile <= 3
    assert result.deciles[0].share > 0.2, "the sparsest decile must be well served"
    assert result.deciles[-1].share == pytest.approx(0.0, abs=1e-9)


def test_warming_a_group_shows_as_negative_benefit() -> None:
    """Clipping warming to zero would let a harmful plan report a clean headline.

    Uniform population, so a decile is exactly a tenth of the cells in row order and the
    warmed band lands squarely inside decile 1. With a density gradient the sparsest
    decile spans a third of the tile, and a four-column band would be diluted by the
    cooling around it - which is a property of the test, not of the estimator.
    """
    rows_per_decile = SHAPE[0] // 10
    delta = np.full(SHAPE, -1.0)
    delta[:rows_per_decile, :] = +2.0

    result = benefit_distribution(delta, _population(gradient=False))

    assert result.deciles[0].share < 0, "a warmed group must carry negative share"
    assert result.deciles[0].mean_delta_c > 0


def test_cooling_where_nobody_lives_is_reported_not_hidden() -> None:
    """'38 % of your cooling landed on empty land' is a finding, not a rounding error."""
    population = _population(gradient=False)
    population[:20, :] = 0.0  # top half uninhabited
    delta = np.full(SHAPE, -1.0)

    result = benefit_distribution(delta, population)

    assert result.uninhabited_cooling_degree_cells > 0
    assert result.uninhabited_fraction == pytest.approx(0.5, abs=0.02)
    # And the inhabited half is still split evenly among the people who are there.
    for decile in result.deciles:
        assert decile.share == pytest.approx(0.10, abs=0.01)


def test_empty_cells_do_not_dilute_a_real_decile() -> None:
    """Uninhabited cells are excluded from the split, not parked in decile 1."""
    population = _population()
    population[:, :10] = 0.0

    result = benefit_distribution(np.full(SHAPE, -1.0), population)

    assert sum(d.cells for d in result.deciles) == int((population > 0).sum())


def test_a_supplied_deprivation_proxy_replaces_density_as_the_stratifier() -> None:
    """The plan's third argument, kept live so a real layer can drop straight in."""
    population = _population(gradient=False)
    # Deprivation rises top to bottom, orthogonal to the (flat) density.
    deprivation = np.repeat(np.linspace(0.0, 1.0, SHAPE[0])[:, None], SHAPE[1], axis=1)
    delta = np.zeros(SHAPE)
    delta[-4:, :] = -3.0  # cooling reaches only the most deprived rows

    result = benefit_distribution(delta, population, deprivation)

    assert result.stratified_by == "deprivation"
    assert result.best_served.decile == 10
    assert result.concentrated


def test_a_tile_with_nobody_on_it_raises_rather_than_dividing_by_zero() -> None:
    with pytest.raises(ValueError, match="no inhabited cell"):
        benefit_distribution(np.full(SHAPE, -1.0), np.zeros(SHAPE))


def test_mismatched_shapes_are_refused() -> None:
    with pytest.raises(ValueError, match="differ"):
        benefit_distribution(np.zeros(SHAPE), np.zeros((10, 10)))


def test_nan_cells_are_ignored_rather_than_poisoning_the_total() -> None:
    """A NaN in the delta field must not make every share NaN."""
    delta = np.full(SHAPE, -1.0)
    delta[0, 0] = np.nan
    population = _population()
    population[1, 1] = np.nan

    result = benefit_distribution(delta, population)

    assert np.isfinite(result.total_person_degrees)
    assert all(np.isfinite(d.share) for d in result.deciles)


def test_a_plan_that_cools_and_warms_equally_reports_its_shares_as_unreliable() -> None:
    """Shares divide by the net benefit, which can vanish.

    Half the tile cooled by 1 degC and half warmed by 1 degC nets out to almost nothing,
    so every decile's share becomes its own value over a denominator near zero. Before
    this guard that produced shares of +/-2010 % and a `concentrated` flag reading 6030 %,
    which would have rendered to the screen as a confident finding.
    """
    delta = np.zeros(SHAPE)
    delta[: SHAPE[0] // 2] = -1.0
    delta[SHAPE[0] // 2 :] = +1.0

    result = benefit_distribution(delta, _population(gradient=False))

    assert result.net_to_gross < 0.2
    assert not result.shares_reliable
    assert not result.concentrated, "a vanishing denominator must not read as concentration"


def test_a_pure_cooling_plan_is_reliable() -> None:
    """The guard must not fire on the normal case, or it would suppress every real answer."""
    result = benefit_distribution(np.full(SHAPE, -1.0), _population())

    assert result.net_to_gross == pytest.approx(1.0)
    assert result.shares_reliable


def test_a_dominant_warming_plan_reports_its_shares_as_unreliable() -> None:
    """`net_to_gross` is a magnitude and does not see sign (F13/F15).

    A tile that reliably warms overall used to pass the same magnitude guard a reliably
    cooling tile does, and be reported as "who received the cooling" with shares like
    104 % of a cooling that never happened. There is no cooling to distribute here.
    """
    result = benefit_distribution(np.full(SHAPE, +1.0), _population())

    # Not a vanishing denominator - the tile warms reliably, just in the wrong direction.
    assert result.net_to_gross == pytest.approx(1.0)
    assert result.total_person_degrees < 0  # negative net = net warming, per module docstring
    assert not result.shares_reliable
    assert not result.concentrated
