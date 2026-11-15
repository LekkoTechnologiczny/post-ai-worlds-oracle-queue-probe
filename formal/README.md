# Bounded formal assurance

TLA+ describes selected rules; TLC explores their finite states. This is executable specification and bounded model checking, not an unbounded theorem, proof of the chapters, empirical prediction or evidence that deployed software follows these rules.

The public edition has its own registry binding and fresh native campaign. Historical results from another source snapshot are not imported as public assurance. Every universe remains `unchecked`: model qualification does not certify a world.

## What the models say

`ScenarioContracts` explores **one accepted claim at a time** across the ten declared universes and 24 selected editorial qualifications. A transfer requires the claim's declared prerequisites; a premise-version change clears assurance until refreshed. It also checks each registered bundle against declared conflicts and admission rules. This does not infer whether a premise is true, discover every contradiction in prose, or check arbitrary combinations of admitted claims.

`OperationalGates` composes `ExpansionContract` and `AuthorityContract` using a shared vocabulary. Phase is finite (0/1); four readiness gates are Boolean; capability, export, alternatives, requests and completed exit are bounded flags. Permission and a public mandate have distinct meanings. The model keeps capability from automatically creating a mandate, and requires all readiness gates before expansion. These are proposed contracts, not a mathematical justification of political legitimacy.

Exit completion requires both exported records and an available alternative. The liveness property assumes those prerequisites already exist and weak fairness for completion. A supplier refusing export before it happens can prevent that premise. This is **conditional completion**, not guaranteed portability against any provider.

## Campaign and controls

There are 12 cases: three clean positive checks, five deliberately broken semantic controls, three reachable witnesses, and one lost-witness composition control. Expected named invariant violations use native exit 12. Those are successful experimental controls, not positive invariant proofs.

The compatible composition reaches an authorised phase-1 state with completed exit despite a later supplier refusal. The incompatible composition forbids public-authorised expansion: TLC exits cleanly on the unreachable-witness invariant, while qualification deliberately rejects the requested reachability (exit 1). Individual properties passing would miss this obstruction.

Inspect [campaign.py](../tools/campaign.py), configurations, captured [evidence](evidence/manifest.json), and model source together. Each capture starts with a producer header binding case, model and input hashes, followed by unmodified native stdout and stderr. This rejects accidental cross-case log substitution. Stored hashes and diagnostics support integrity and consistency review; they do not cryptographically attest who ran a process. A contributor changing a model should rerun it, rather than relabel old logs.

## Reproduce

Use Python3.11+ and the installed shared `tla-queue` client. The JDK and pinned
TLC1.7.4 run on Oracle; local authors do not download or execute TLC.

```sh
python3 tools/run_models.py --java /usr/bin/java --jar /opt/tla/tla2tools-1.7.4.jar
python3 tools/check.py
python3 -m unittest discover -s tests -v
```

`--jar` and `--java` are Oracle-side paths. The client snapshots the producer,
declared models/configurations and registry, submits an immutable campaign and
waits for its actual result. No client/backend means explicit failure, never
local TLC fallback. Native execution requires the Linux queue worker identity
and matching job cgroup. The worker verifies JAR SHA-256
`936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`.

Every campaign retains raw logs, source/tool bindings and its manifest on Oracle.
By default, exactly twelve logs, the manifest and generated RegistrySnapshot are
safely fetched into the selected `--root` (the repository by default), preserving
unrelated files and rejecting links/path escapes. `--no-fetch` keeps evidence
only on Oracle. Failed partial campaigns cannot overwrite a complete local
campaign or qualify as success. Historical evidence stays in Git history; do
not relabel prior logs with changed source hashes.

Native output must contain no personal filesystem identities; it is not redacted
after hashing. The ordinary retained-evidence checker remains offline and needs
no Java, account or producer installation. Changing any of the four producer
sources, including `remote_models.py`, invalidates old source-bound evidence and
requires a real new campaign. Models, bounds, flags and semantic controls remain
unchanged by this transport.

[Shared worlds](../worlds/README.md) · [Contribution guidance](../CONTRIBUTING.md)

## Oracle execution

All push heads (main, feature branches and tags), pull requests and explicit
manual main verification use the shared Oracle queue. Hosted checks perform
structural/navigation/site work. A trusted default-main dispatcher then runs
native production and the complete unchanged candidate test suite in separate
sandboxed jobs before publishing the combined exact-head `validate` result.
Fork approval is preserved: no PR job is submitted until its approved hosted
source workflow completes. See [runner operation and authority boundary](../docs/oracle-runner.md).

Prediction proposals may include formal work, but its absence is nonblocking;
the [forecasting standard](../docs/forecasting-guide.md) governs empirical reasoning.
