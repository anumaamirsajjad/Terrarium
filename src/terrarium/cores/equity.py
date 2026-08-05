"""Who actually gets the cooling.

A modelled ΔLST field says how much a tile cools. It does not say *who benefits*, and
those are different questions with different answers: cooling a golf course and cooling a
dense neighbourhood produce similar-looking maps and completely different outcomes.

This is the panel that critiques the user's own plan, so it has to be capable of returning
an unflattering answer. The design choices below are all in service of that.

### Person-degrees, not degrees

The unit is **population x cooling**: one degree delivered to a cell holding 500 people is
worth 500 person-degrees, the same cell empty is worth nothing. That is what makes the
distribution answerable at all. Cooling on uninhabited land is not discarded — it is
reported separately, because "38 % of your cooling landed where nobody lives" is exactly
the sort of finding this panel exists to surface.

### Deciles of *people*, not of cells

Each group holds a tenth of the tile's residents, not a tenth of its pixels. This is what
makes the output readable without arithmetic: **perfectly equal sharing is 10 % each**, so
any departure is visible at a glance. Equal-pixel deciles would put most of the population
in one bucket and make every number need a caveat.

### Signed, so warming is not silently dropped

`delta_lst` is negative for cooling, and this returns positive person-degrees for it. A
cell that *warms* contributes negative benefit rather than being clipped to zero: a plan
that cools the rich and warms the poor must not be able to report the same headline as one
that simply cools less.

Pure, like everything in `cores/`: arrays in, numbers out. No I/O, no config, no globals.

### On the deprivation proxy

The plan's signature is `benefit_distribution(delta_lst, population, deprivation_proxy)`
and its default assumption was nightlights plus building density. **Neither VIIRS
nightlights nor GHSL built-up is on Planetary Computer**, so a deprivation layer needs a
new external source, and the cube does not carry one today. The argument is kept because
the question is the right one; it is `None` until a real layer exists.

What it is emphatically *not* doing meanwhile is standing in NDBI or built-up density and
calling that deprivation. Dense built-up in Lahore contains both the wealthiest and the
poorest districts, so that substitution would produce a confident equity claim the data
cannot support. Stratifying by population density instead answers a narrower but honest
question: does the cooling reach the crowded places, where heat stress is worst?
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel, ConfigDict

# Ten groups, because "decile" is what a policy audience already reads fluently.
DEFAULT_GROUPS = 10
# Concentration flag: the three best-served deciles holding more than this share of the
# benefit. Even sharing puts three deciles at 30 %, so a majority in three of ten is a
# genuine skew rather than a rounding artefact.
CONCENTRATION_THRESHOLD = 0.5
# Below this ratio of net to gross benefit, `share` divides by a denominator small enough
# that the numbers stop meaning anything. Chosen well above the pathological case (a
# perfectly cancelling plan, ratio ~0) and well below any realistic planting scenario,
# where warming is a rounding error and the ratio sits near 1.
RELIABLE_NET_FRACTION = 0.2


class DecileShare(BaseModel):
    """One tenth of the population, and what the intervention did for them."""

    model_config = ConfigDict(frozen=True)

    # 1 = least dense (or least deprived, when a proxy is supplied), 10 = most.
    decile: int
    people: float
    cells: int
    # Population-weighted mean ΔLST in °C. Negative is cooling. Weighted, because an
    # unweighted mean over cells would let empty cells outvote crowded ones.
    mean_delta_c: float
    # Share of the tile's total person-degrees. 0.10 is an even share. Absolute
    # person-degrees are `share * total_person_degrees` and mean density is
    # `people / cells`, so neither is carried separately - a derived field that nothing
    # reads is one more thing to keep consistent for no benefit.
    share: float


class BenefitDistribution(BaseModel):
    """How one intervention's cooling was distributed across the people it reaches."""

    model_config = ConfigDict(frozen=True)

    deciles: list[DecileShare]
    total_person_degrees: float
    # Sum of |person-degrees|: how much temperature moved in total, regardless of
    # direction. Only differs from the net when a plan warms somewhere.
    gross_person_degrees: float
    population_covered: float
    stratified_by: str

    # Cooling measured as degree-cells (sum of -delta over cells, every cell being 100 m
    # square), split by whether anyone lives there. Deliberately *not* in person-degrees:
    # an empty cell has zero residents, so its person-degrees are zero by construction and
    # the question "how much of my cooling reached nobody" would answer itself with 0.
    # This is the one place the unit has to be area, not people.
    inhabited_cooling_degree_cells: float
    uninhabited_cooling_degree_cells: float

    @property
    def uninhabited_fraction(self) -> float:
        """Of all cooling produced, the fraction landing on cells where nobody lives."""
        total = self.inhabited_cooling_degree_cells + self.uninhabited_cooling_degree_cells
        return self.uninhabited_cooling_degree_cells / total if total else 0.0

    @property
    def net_to_gross(self) -> float:
        """Net benefit as a fraction of all temperature movement. 1.0 = pure cooling.

        Near zero means the plan cools and warms in almost equal measure, so the net
        total is a small difference between large numbers.
        """
        if not self.gross_person_degrees:
            return 0.0
        return abs(self.total_person_degrees) / self.gross_person_degrees

    @property
    def shares_reliable(self) -> bool:
        """Whether `share` means anything at all for this intervention.

        Shares divide by the *net* benefit. When a plan cools and warms nearly equally
        that denominator collapses and the shares blow up - a tile split half cooling,
        half warming produced shares of +/-2010 % and a `concentrated` flag of 6030 %,
        which would have rendered to the screen as a confident finding. Below this ratio
        the split is not reportable and callers must say so rather than draw the bars.
        """
        return self.net_to_gross >= RELIABLE_NET_FRACTION

    @property
    def top_three_share(self) -> float:
        """Share held by the three best-served deciles. Even sharing is 0.30."""
        return float(sum(sorted((d.share for d in self.deciles), reverse=True)[:3]))

    @property
    def concentrated(self) -> bool:
        """True when a majority of the benefit reaches three deciles out of ten.

        Never true on an unreliable split: a ratio of large numbers over a vanishing
        denominator clears any threshold, and reporting that as concentration would be
        an artefact presented as a finding.
        """
        return self.shares_reliable and self.top_three_share > CONCENTRATION_THRESHOLD

    @property
    def worst_served(self) -> DecileShare:
        return min(self.deciles, key=lambda d: d.share)

    @property
    def best_served(self) -> DecileShare:
        return max(self.deciles, key=lambda d: d.share)


def benefit_distribution(
    delta_lst: np.ndarray,
    population: np.ndarray,
    deprivation: np.ndarray | None = None,
    *,
    n_groups: int = DEFAULT_GROUPS,
) -> BenefitDistribution:
    """Distribute an intervention's cooling across population groups.

    `delta_lst` and `population` are `(y, x)` on the canonical grid; negative delta is
    cooling. `deprivation` optionally replaces population density as the stratifier — see
    the module docstring for why it is `None` today.

    Cells with no residents are excluded from the deciles and reported separately: they
    hold benefit but nobody to receive it, and folding them into a decile would dilute a
    real group's share with empty land.
    """
    delta = np.asarray(delta_lst, dtype="float64").reshape(-1)
    people = np.asarray(population, dtype="float64").reshape(-1)
    if delta.shape != people.shape:
        raise ValueError(f"delta {delta.shape} and population {people.shape} differ")

    usable = np.isfinite(delta) & np.isfinite(people)
    inhabited = usable & (people > 0)

    # Cooling is positive benefit, so flip the sign of a negative delta.
    person_degrees = np.where(usable, -delta * np.maximum(people, 0.0), 0.0)
    cooling = np.where(usable, -delta, 0.0)
    uninhabited_cooling = float(cooling[usable & ~inhabited].sum())
    inhabited_cooling = float(cooling[inhabited].sum())

    if not inhabited.any():
        raise ValueError("no inhabited cell has a finite delta; nothing to distribute")

    if deprivation is None:
        stratifier, label = people, "population density"
    else:
        stratifier = np.asarray(deprivation, dtype="float64").reshape(-1)
        if stratifier.shape != delta.shape:
            raise ValueError("deprivation must be the same shape as delta_lst")
        label = "deprivation"

    index = np.nonzero(inhabited)[0]
    # Stable sort so cells tying on the stratifier keep a deterministic order - otherwise
    # the same cube can produce different deciles between runs.
    order = index[np.argsort(stratifier[index], kind="stable")]

    pop_sorted = people[order]
    cumulative = np.cumsum(pop_sorted)
    total_people = float(cumulative[-1])

    # Assign by each cell's *midpoint* in the cumulative population, not its upper edge:
    # using the edge pushes every boundary-straddling cell into the higher group, which
    # systematically inflates the top decile.
    midpoint = cumulative - pop_sorted / 2.0
    group = np.minimum((midpoint / total_people * n_groups).astype(int), n_groups - 1)

    contributions = person_degrees[order]
    total = float(contributions.sum())
    gross = float(np.abs(person_degrees[usable]).sum())

    deciles: list[DecileShare] = []
    for number in range(n_groups):
        in_group = group == number
        group_people = float(pop_sorted[in_group].sum())
        group_degrees = float(contributions[in_group].sum())
        deciles.append(
            DecileShare(
                decile=number + 1,
                people=group_people,
                cells=int(in_group.sum()),
                # Person-degrees back to degrees: divide by the people who received them.
                mean_delta_c=-group_degrees / group_people if group_people else 0.0,
                share=group_degrees / total if total else 0.0,
            )
        )

    return BenefitDistribution(
        deciles=deciles,
        total_person_degrees=total,
        gross_person_degrees=gross,
        population_covered=total_people,
        stratified_by=label,
        inhabited_cooling_degree_cells=inhabited_cooling,
        uninhabited_cooling_degree_cells=uninhabited_cooling,
    )
