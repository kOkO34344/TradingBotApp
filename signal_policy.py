#!/usr/bin/env python3
"""
signal_policy.py — SINGLE SOURCE OF TRUTH for which trading signal is allowed
to run, and which one is the project's default.

Kronos is the project's main signal (owner decision, 2026-07-28). Momentum is
DISABLED and will not compute — in any live path, by any caller, whether it was
selected on a command line, left in trader_settings.json, or reached through a
`.get(..., default)` fallback — unless a caller deliberately passes
`allow_momentum=True`.

WHY THE GATE RATHER THAN JUST A CHANGED DEFAULT
    A default is a preference; the owner's instruction was "never run momentum
    again until I explicitly say I want it". Defaults drift: trader_settings.json
    can be edited, an autotrade block can be stale, and this codebase already had
    THREE separate `signal = ...get("signal", "momentum")` fallbacks that would
    quietly resurrect it. A gate that raises cannot drift. This deliberately
    mirrors rule 1's `allow_live=True` and rule 2's `allow_no_stop=True`: the
    dangerous thing is reachable, but only by someone typing the words.

HONEST NOTE ON THE EVIDENCE (do not quietly delete this)
    This choice runs AGAINST the project's own measurements, and that's the
    owner's call to make, not the code's:
      - Momentum rotation is the ONLY strategy family that ever earned Phase 3
        (~18.5% CAGR vs SPY 16%, max DD -21.7% vs -33.7%).
      - Kronos walk-forward (2026-07-23, post-pretraining-cutoff window):
        Spearman IC 0.036, 50.0% directional hit rate — no measurable skill.
      - Hourly IC screen (2026-07-24): Kronos IC -0.081 / 46.4% hit rate,
        actually WORSE than the momentum-style baseline's -0.037 / 48.5%.
    So Kronos being "the main focus" is a research direction, not a validated
    edge, and nothing here should be read as evidence it works. Rules 4 and 5
    still apply in full: paper only, graded evidence, negative results reported.

TO RE-ENABLE MOMENTUM (owner, in a session where you've said so):
    python3 paper_trader.py --signal momentum --allow-momentum
  or for the unattended runner, set trader_settings.json's
    "autotrade": {"signal": "momentum", "allow_momentum": true}
  Without that second explicit key the runner refuses to fire and says so,
  rather than silently trading a signal you asked it not to trade.

Offline check: python3 signal_policy.py
"""

DEFAULT_SIGNAL = "kronos"
KNOWN_SIGNALS = ("kronos", "momentum")

# Signals that will not compute without a deliberate allow_momentum=True.
DISABLED_SIGNALS = ("momentum",)


class SignalDisabled(RuntimeError):
    """Raised when a disabled signal is requested without explicit opt-in."""


def _refusal(signal: str, context: str) -> str:
    return (
        f"Signal '{signal}' is DISABLED ({context}).\n"
        f"The owner's standing instruction (2026-07-28) is that momentum does not "
        f"run again until they explicitly ask for it in that session.\n"
        f"Kronos is the project's default signal. If you ARE the owner and you do "
        f"want momentum right now:\n"
        f"  python3 paper_trader.py --signal momentum --allow-momentum\n"
        f"  (unattended: add \"allow_momentum\": true to trader_settings.json's "
        f"\"autotrade\" block)"
    )


def assert_allowed(signal: str, allow_momentum: bool = False,
                   context: str = "live signal") -> str:
    """Return `signal` if it may run, else raise SignalDisabled.

    Call this at the TOP of any function that computes a ranking used to place
    orders — not at the call site. A gate the caller has to remember is a gate
    that gets forgotten.
    """
    if signal not in KNOWN_SIGNALS:
        raise ValueError(f"Unknown signal '{signal}' — expected one of {KNOWN_SIGNALS}")
    if signal in DISABLED_SIGNALS and not allow_momentum:
        raise SignalDisabled(_refusal(signal, context))
    return signal


def resolve_signal(settings: dict | None = None, requested: str | None = None) -> str:
    """The signal to use: an explicit request wins, then settings, then Kronos.

    Never returns a disabled signal from a *fallback* — an absent or unreadable
    setting resolves to DEFAULT_SIGNAL, so config drift can't reintroduce
    momentum by accident. An explicitly stored "momentum" IS returned, so the
    caller can gate it and report an honest refusal instead of silently
    substituting a different signal than the one configured.
    """
    if requested:
        return requested
    stored = (settings or {}).get("signal")
    if not stored:
        stored = ((settings or {}).get("autotrade") or {}).get("signal")
    return stored or DEFAULT_SIGNAL


def momentum_opt_in(settings: dict | None = None) -> bool:
    """True only if trader_settings.json carries the deliberate opt-in key."""
    return bool(((settings or {}).get("autotrade") or {}).get("allow_momentum", False))


def _selftest() -> int:
    failures = []

    def check(name, cond):
        print(("  PASS  " if cond else "  FAIL  ") + name)
        if not cond:
            failures.append(name)

    def raises(fn, exc=SignalDisabled):
        try:
            fn()
            return False
        except exc:
            return True

    check("kronos is the default", DEFAULT_SIGNAL == "kronos")
    check("kronos always allowed", assert_allowed("kronos") == "kronos")
    check("momentum refused by default", raises(lambda: assert_allowed("momentum")))
    check("momentum allowed with explicit opt-in",
          assert_allowed("momentum", allow_momentum=True) == "momentum")
    check("unknown signal rejected",
          raises(lambda: assert_allowed("astrology"), ValueError))

    # resolve_signal: fallbacks must never manufacture momentum
    check("empty settings -> kronos", resolve_signal({}) == "kronos")
    check("missing autotrade signal -> kronos",
          resolve_signal({"autotrade": {"enabled": True}}) == "kronos")
    check("None settings -> kronos", resolve_signal(None) == "kronos")
    check("explicit request wins",
          resolve_signal({"signal": "kronos"}, requested="momentum") == "momentum")
    check("stored momentum is surfaced, not silently swapped",
          resolve_signal({"autotrade": {"signal": "momentum"}}) == "momentum")
    check("top-level signal beats autotrade block",
          resolve_signal({"signal": "kronos", "autotrade": {"signal": "momentum"}}) == "kronos")

    check("opt-in flag off by default", not momentum_opt_in({"autotrade": {}}))
    check("opt-in flag read when set",
          momentum_opt_in({"autotrade": {"allow_momentum": True}}))

    # the refusal text has to tell you how to undo it
    msg = _refusal("momentum", "test")
    check("refusal explains the opt-in", "--allow-momentum" in msg and "owner" in msg)

    print(f"\n{'ALL PASS' if not failures else f'{len(failures)} FAILURES: {failures}'}")
    return 1 if failures else 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
