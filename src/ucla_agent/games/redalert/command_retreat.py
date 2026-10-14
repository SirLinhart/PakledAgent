"""The script's retreat override over the model commander: main pulled out of a fight it keeps losing.

A small model commanding main did not pull it out of a lost fight: offered
`retreat home` every time it was losing, it never picked it. So while the
commander commands main (see `commander`), the script watches main's fight
where it stands, the `fight_here` figure of main's question (see
`command_army`), in every cycle, the figures those of `[commander]`:

- **Taking main.** Once the figure has stayed below retreat_ratio in every
  cycle for retreat_confirm_ticks, the override takes main: main goes to the
  staging point, whatever the model picked, and the forward gate closes (see
  `command_gate`). A cycle with no enemy, or a figure at retreat_ratio or
  above, starts the count again.
- **While it holds** the model's answers are not carried out.
- **Letting go.** It ends once there is no enemy (`no_enemy`), once the
  figure is at least resume_ratio (`resumed`), or once main's centroid is
  within `HOME_CELLS` of the staging point (`home`).

No enemy is what `fight_here` reads as `no enemy`: no enemy unit within
`ENEMY_NEAR_CELLS` of main's body and no ground defense whose weapon reaches
it. A fight lost to defenses alone holds main as one lost to an army does.

The commander writes a `commander` event of kind `override_retreat`, with the
figure, when the override takes main, and one of kind `override_end`, with
the reason, when it lets go; `gather` is main's standing action from then on.

Nothing here reads a file, a clock or the network.
"""

from __future__ import annotations

from ucla_agent.games.redalert.tuning import CommanderTuning

# What the override does in a cycle: take main, or let it go for one of these reasons.
TAKEN = "taken"
RESUMED = "resumed"
NO_ENEMY = "no_enemy"
HOME = "home"
# main's centroid this near the staging point is home.
HOME_CELLS = 6


class RetreatOverride:
    """Whether the script pulls main out of its fight, and since when its fight has been lost. See the module's note."""

    def __init__(self) -> None:
        self.is_active = False
        # The first tick of the cycles in a row in which main's fight was below retreat_ratio; None for none.
        self._losing_since: int | None = None

    def follow(self, fight_ratio: float | None, is_home: bool, tick: int, tuning: CommanderTuning) -> str | None:
        """Take in main's fight of a cycle (None for no enemy, unit or defense) and whether main is home; TAKEN when
        the override takes main in this cycle, the reason when it lets main go, and None when neither happens."""
        if self.is_active:
            reason = _letting_go(fight_ratio, is_home, tuning)
            if reason is not None:
                self.is_active, self._losing_since = False, None
            return reason
        if fight_ratio is None or fight_ratio >= tuning.retreat_ratio:
            self._losing_since = None
            return None
        if self._losing_since is None:
            self._losing_since = tick
        if tick - self._losing_since < tuning.retreat_confirm_ticks:
            return None
        self.is_active = True
        return TAKEN


def _letting_go(fight_ratio: float | None, is_home: bool, tuning: CommanderTuning) -> str | None:
    """Why the override lets main go in a cycle: no enemy, its fight won back, or main home; None to hold on."""
    if fight_ratio is None:
        return NO_ENEMY
    if fight_ratio >= tuning.resume_ratio:
        return RESUMED
    return HOME if is_home else None
