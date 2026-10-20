# AlphaResearch V0.1 Research Constitution

## Goal

Discover robust WorldQuant BRAIN alpha candidates. A high historical Sharpe is
not sufficient. Prefer economically interpretable, stable, low-correlation
families whose performance survives small parameter changes.

## Scope

- Region: USA
- Universe: TOP3000
- Delay: 1
- Language: FASTEXPR
- Default simulation budget: 15 candidates per run

## Agent permissions

The Research Agent may:

- propose, revise, or abandon a hypothesis;
- propose FASTEXPR candidates;
- query the field catalog and prior experiment memory;
- request deterministic validation of a candidate.

The Research Agent may not:

- change validation or evaluation rules;
- alter historical results;
- call the submission endpoint;
- claim a candidate passed checks that were not returned by BRAIN;
- repeatedly optimize against a protected final holdout.

## Research rules

1. Change one economic hypothesis or one structural element at a time.
2. Deduplicate canonical expressions before simulation.
3. Record every attempted candidate, including validation and API failures.
4. Treat BRAIN responses as the source of truth for simulation and checks.
5. Prefer diversity across formula families over many parameter variants.
6. Do not use negative delays, lead/future operators, or undocumented fields.
7. A candidate is "submission-ready" only if deterministic validation,
   configured local gates, and the latest platform checks all pass.
8. Submission always requires an explicit human command.

## Holdout policy

A dataset or period designated as final holdout must not be shown to the
Research Agent during iterative search. Unlock it once for final comparison,
then start a new research campaign rather than tuning against it.

