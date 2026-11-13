"""The smell baseline (Fowler's catalogue, as adapted by mattpocock/skills `engineering/code-review`, MIT).
Raised only as labelled judgement calls ("possible Feature Envy"), severity at most optional; a team's stated
standards override it."""

BASELINE = """Smell baseline (judgement calls; say "possible <smell>"; severity optional at most):
- Mysterious Name: a name that does not say what the thing does.
- Duplicated Code: the same logic added twice in the diff.
- Feature Envy: a function that mostly uses another object's data.
- Data Clumps: the same group of values passed around together.
- Primitive Obsession: a primitive standing in for a domain concept (money as float, ids as raw strings).
- Repeated Switches: the same switch/if-chain on a type in several places.
- Shotgun Surgery: one change needing edits in many places in the diff.
- Divergent Change: one module changed for several unrelated reasons.
- Speculative Generality: abstraction or parameters with no current use.
- Message Chains: a.b().c().d() reaching through objects.
- Middle Man: a class or function that only delegates.
- Refused Bequest: a subclass that ignores or overrides most of what it inherits."""
