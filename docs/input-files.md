# Canonical input files

AEF modifying commands consume explicit strict JSON. Object keys must be text,
numbers must be finite, and duplicate identities or unknown fields in closed
structures are rejected. Validate a plan with `--dry-run` before applying it.

## DISCOVER connector snapshot

Use [connectors.json](examples/connectors.json):

```console
aef discover --snapshot docs/examples/connectors.json --dry-run
aef discover --snapshot docs/examples/connectors.json
```

The root contains only the required `connectors` array; DISCOVER V1 has no
protocol field. Every connector requires a unique non-empty `id`, one of the
statuses `available`, `unavailable`, `deprecated`, `unknown`, or `restricted`,
and a `capabilities` array.

Each capability requires a unique non-empty `id`, a non-empty `operation`, a
`risk` from `R0` through `R4`, and boolean `reversible`. Optional `available`
is boolean and defaults to true. Optional `native_metadata` is opaque strict
JSON: nested snapshot keys replace matching values while existing omitted keys
are preserved.

Do not put approval, authority, policy, Trust, level, or permission fields in a
snapshot. DISCOVER never interprets metadata as authority. A connector omitted
from a later snapshot is preserved as `unavailable`; its capabilities are
preserved with `available: false`.

## CONSOLIDATE rule reviews

Use [reviews.json](examples/reviews.json):

```console
aef consolidate --reviews docs/examples/reviews.json --dry-run
aef consolidate --reviews docs/examples/reviews.json
```

The closed root is `{"protocol":"aef.consolidate/v1","reviews":[...]}`.
Every review requires unique non-empty `id` and `rule_id`, an action, a
non-empty `reason`, and unique `evidence_ids`. Evidence must resolve exactly
once in the same `knowledge.json`; ambiguous or missing evidence blocks the
whole batch.

Actions are:

- `keep`: records no change, permits an empty evidence list, and forbids
  `approval`.
- `specialize`: requires non-empty object `context` and explicit approval.
- `supersede`: requires explicit approval plus a closed `replacement` object.
- `retire`: requires explicit approval.

Every modifying action requires exactly:

```json
{
  "approved": true,
  "source": "human",
  "actor": "Alex Example",
  "approved_at": "2026-08-14T14:00:00Z"
}
```

The timestamp is strict RFC 3339. A superseding replacement contains exactly
`id`, `type: "rule"`, `status: "active"`, non-empty `pattern_key`, and
`evidence_ids` equal as a set to the review evidence. Its identifier must be
distinct from the replaced rule and all relevant knowledge/review identities.

Replaying the same `review_id` with equivalent content returns `NO_CHANGE`.
Reusing it with different content is a conflict. Existing knowledge and history
are preserved, and consolidation grants no authority.

## EVALUATE human decisions

First obtain the exact recommendation and digests:

```console
aef --json evaluate --list
```

Then adapt [evaluation-decisions.json](examples/evaluation-decisions.json):

```console
aef evaluate --decisions docs/examples/evaluation-decisions.json --dry-run
aef evaluate --decisions docs/examples/evaluation-decisions.json
```

The closed root uses protocol `aef.evaluate/v1` and a `decisions` array. Each
decision requires a unique non-empty `id`, exact `recommendation_id`, non-empty
`reason`, and the recommendation's `expected_evidence_digest`.

`decision: "approve"` additionally requires the recomputed
`expected_current_evidence_digest` and an `approval` containing exactly
`approved: true`, `source: "human"`, non-empty `actor`, and strict RFC 3339
`approved_at`. `decision: "reject"` instead requires a `rejection` containing
exactly `rejected: true`, `source: "human"`, `actor`, and `rejected_at`; it does
not contain `expected_current_evidence_digest`.

A file is explicit evidence of a human decision only because it contains the
complete human record. Mere file presence is never approval. EVALUATE
recomputes readiness and blocks stale evidence. It never advances more than one
level.

## RECORD declared-fact submission

Use [recording.json](examples/recording.json):

```console
aef record --recording docs/examples/recording.json --dry-run
aef record --recording docs/examples/recording.json
```

The closed root uses protocol `aef.record.submit/v1`. It requires a
filesystem-safe `record_id`, a canonical RFC 3339 UTC `recorded_at` ending with
`Z`, a `declared_by` object (`kind` is `human` or `agent`, plus a non-empty
`identifier`), and a `payload` with `context` plus the four collections
`actions`, `outcomes`, `incidents`, and `evidence`. At least one collection
must contain an item. Optional `external_metrics` may declare `duration`,
`tokens_in`, `tokens_out`, or `cost` with their contractual units.

Do not include a `digest`. AEF computes it when persisting `aef.record/v1`.
RECORD stores the declaration only; it does not create scores, XP,
competencies, rules, or evaluations.

`--dry-run` creates neither `.agent/records/` nor the record file. Replaying
the same valid document against a valid matching file returns `NO_CHANGE`
without rewriting. Reusing `record_id` with different content is a conflict
and does not rewrite the existing file.

## Refresh and recovery

`aef evaluate --list` is strictly read-only. Refresh may change recommendation
statuses, so inspect it first:

```console
aef evaluate --refresh --dry-run
aef evaluate --refresh
```

Recovery may finalize or roll back several files. Run it only after an explicit
human request, always beginning with:

```console
aef evaluate --recover --dry-run
aef evaluate --recover
```

When recovery is required, all other modifying operations are blocked. Never
edit or delete the transaction journal manually.
