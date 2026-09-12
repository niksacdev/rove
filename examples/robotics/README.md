# Action-dependent synthetic robotics campaign

This example moves a point along a one-dimensional axis using candidate trajectory
deltas. It runs through ROVE's existing act, simulator and configured verifier
interfaces. The two deterministic trajectory fixtures are test inputs, not VLA
model-quality benchmarks. The illustration is a UI placeholder; the structured
world definition determines the actual task.

From the repository root with the project environment installed:

```sh
python examples/robotics/run.py --store /tmp/rove-synthetic-demo
```

The runner imports three cases, freezes a synthetic episode success contract, then
runs two strategies with three repetitions each: 18 planned trials. The strategy
moving away fails all three cases. Moving toward the target succeeds on the clear
case and after the injected disturbance; crossing the forbidden interval fails.
A 60% task-success target therefore separates the two strategies. Repetition is
deterministic software validation, not evidence of stochastic model reliability.

Reports are written under the chosen store's `reports` directory. Use that store
with the normal local API to inspect the archived episode samples. Each execution
has a fresh reset ID; recorded actions and resulting positions are stored as
content-addressed JSON assets. No live model, cloud endpoint or physical robot is
used. Each invocation creates a fresh campaign and cases.

The configuration and fixture definitions are original ROVE examples distributed
under the repository license. The placeholder image comes from the repository's
[synthetic customer examples](../customer-cases/README.md).

See the [robotics evidence specification](../../docs/product/robotics-evidence.md)
for episode schemas, approved annotation bindings and metric denominators.
