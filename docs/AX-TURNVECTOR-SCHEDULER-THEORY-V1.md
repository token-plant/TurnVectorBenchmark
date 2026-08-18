# AX Engine vs TurnVector Pure Scheduling Theory V1

## Status

| Field | Value |
| --- | --- |
| Evidence class | Analytic model derived from scheduler contracts |
| Measured benchmark result | No |
| Discrete-event simulation result | No |
| AX Engine source | `defai-digital/ax-engine@16c28557edf2b8469fe7f247505ae038d54472c7` |
| TurnVector design authority | Tracked scheduling ADRs and `CONTEXT.md` present at `token-plant/TurnVector@35d9109d4e3f9686c5b9acfd5324f5bb31bf003c` |
| Primary output | Parameterized equations, not a single product-speed claim |

This document preserves the first analytic comparison model. It is a future
benchmark input, not evidence that either runtime has achieved the calculated
envelope. In particular, it must not be cited as "TurnVector is N times faster
than AX Engine."

## Plain-language result

AX Engine and TurnVector both serialize the comparison to one device Turn at a
time. The central difference is how they decide who receives the next Turn:

- AX Engine's exclusive arbiter rotates between waiting model IDs. One expensive
  Turn and one cheap Turn each count as one visit.
- TurnVector charges the actual synchronized Engine Service divided by Model
  Weight, then uses Latest Safe Start to temporarily put a timing obligation
  ahead of normal weighted order.

That difference does not create free compute. If both systems execute the same
Turns, switch equally often, and have equal scheduling overhead, their raw
throughput is the same. TurnVector can improve SLO goodput only when its bounded
chunk choice, deadline protection, or configured service weights prevent work
from missing an SLO that the AX rotation would miss. TurnVector can also lose
capacity when its extra fresh-plan overhead is larger or when configured weights
do not match offered demand.

## Fixed comparison boundary

The model fixes the following facts for both schedulers:

- the same request arrival sequence, model revisions, output work, same-model
  Batch candidates, and true Engine Service cost surface;
- `max_overlapping_turns = 1`; AX's experimental concurrent-holder mode is out
  of scope;
- all models are resident and memory is sufficient;
- no load, unload, KV migration, cache hit, reclaim, failure, or cancellation;
- switch cost, chunk choice, scheduler overhead, and fresh replanning are in
  scope;
- Engine Service is a theoretical oracle value. AX guard hold time and
  TurnVector synchronized Engine Service are not called GPU occupancy;
- same-model Batch Formation is common input, so batching implementation
  differences are not attributed to global scheduling.

## Terms in ordinary language

| Term | Meaning in this model |
| --- | --- |
| `lambda` | Offered requests per unit time. |
| `lambda*_P` | Highest `lambda` at which scheduler `P` meets the chosen request-level SLO success threshold. |
| `rho` | Offered useful Engine Service divided by one unit of exclusive-server time; `rho=1` is oracle work saturation before switch and scheduler overhead. |
| Capacity ratio | `lambda*_TV / lambda*_AX`; above 1 favors TurnVector, below 1 favors AX. |
| Goodput | Offered requests that satisfy every applicable SLO, per unit time. |
| TTFT | Time from request acceptance to its first visible token. |
| TPOT | Time between consecutive visible output tokens. |
| Progress gap | Time a safe runnable Standard or Background request waits without committed progress. |
| Turn | One bounded, synchronized device operation after which global scheduling can run again. |
| Switch cost | Time paid when consecutive Turns use different models. |
| Fresh replan | Rebuilding the global decision after a Turn or invalidation. |
| Model Ledger | TurnVector's weighted service account. A Turn adds actual Engine Service divided by Model Weight. |
| Latest Safe Start | The latest instant a candidate can start while its conservative complete cost still fits its timing obligation. |
| Urgent Set | Safe candidates whose Latest Safe Start has arrived. |

## Symbols

| Symbol | Definition |
| --- | --- |
| `D_i(b, k)` | True Decode Turn Engine Service for model `i`, batch `b`, and KV state `k`. |
| `P_i(q, x)` | True Prefill Engine Service for a `q`-token chunk beginning at position `x`. |
| `S_ij` | Cost of switching from model `i` to model `j`. |
| `h_AX`, `h_TV` | Direct scheduler/runtime overhead per fresh decision outside Engine Service. |
| `w_i` | TurnVector Model Weight. |
| `c_i` | True Engine Service of one representative Turn for model `i`. |
| `L_F`, `L_S`, `L_P` | First-token, stream-gap, and progress obligations. |
| `epsilon` | Relative Cost Profile error bound. |
| `Q` | Common finite set of legal chunk sizes. |

All time quantities must use the same unit. Ratios are dimensionless.

For request-class mix `p_r` and true useful Engine Service `e_r` per request,
this document normalizes load as

$$
\rho=\lambda\sum_r p_r e_r.
$$

This definition keeps `rho` common across schedulers. Their actual saturation
can occur below 1 after switch and scheduler overhead are added.

## AX abstraction

With all `m` models continuously waiting and one exclusive holder, AX grants
one visit to each model in deterministic cyclic order. For one representative
Turn per model, the cycle is

$$
C_{AX} = \sum_{i=1}^{m}(c_i + h_{AX})
       + \sum_{i=1}^{m}S_{i,next(i)}.
$$

Model `i` completes one representative Turn per cycle. Its long-run Engine
Service share is therefore

$$
\phi^{AX}_i = \frac{c_i}{\sum_j c_j}.
$$

This is equal-turn rotation, not equal-time or configured-weight service.

Two Prefill controls are retained as analytic baselines:

- `AX-F`: fixed `q=64` while a sibling is active and `q=256` otherwise;
- `AX-A`: initial `q=64`, target `40 ms`, clamp `1..256`, and sibling activity
  grace `250 ms`.

For the adaptive controller, let the previous chunk have `q_k` tokens and take
`t_k`. The source computes an integer microseconds-per-token estimate and target

$$
u_k = \left\lceil\frac{t_k}{q_k}\right\rceil,
\qquad
q^{target}_{k+1}=clamp\left(\left\lfloor\frac{40000}{u_k}\right\rfloor,1,256\right).
$$

If `t_k > 40000`, the next quantum snaps to the target. Otherwise it moves
halfway toward the target, with at least one token of movement. The pinned AX
source also contains an exclusive long-Prefill branch that can pin the quantum
to the configured chunk limit. A future empirical comparison must record which
branch actually executed; `AX-F` and `AX-A` here are controlled mathematical
baselines, not an assertion that both are simultaneously active in production.

## TurnVector abstraction

For continuously runnable models, TurnVector's non-urgent selection tends
toward the configured Engine Service shares

$$
\phi^{TV}_i = \frac{w_i}{\sum_j w_j}.
$$

Every completed Turn remains charged to the Model Ledger, including an urgent
Turn. Urgency changes order temporarily; it does not create free service.

For a candidate with timing obligation `L` and conservative complete cost
`C`,

$$
LatestSafeStart = L - C.
$$

When current monotonic time reaches that instant, the candidate enters the
Urgent Set. TurnVector still cannot interrupt the Turn already in flight.

For Cost Profile error, this document uses

$$
(1-\epsilon)c \le \hat c \le (1+\epsilon)c,
\qquad 0 \le \epsilon < 1.
$$

Therefore a safe upper bound on true cost is

$$
c^+ = \frac{\hat c}{1-\epsilon}.
$$

The conservative chunk baseline selects the largest common chunk whose upper
cost fits a fixed Decode-Turn service target. The ideal analytic envelope
selects the largest common chunk whose upper occupied time does not carry any
sibling past its Latest Safe Start. Neither is a measured implementation
result.

## Scenario equations

### S0: one-model overhead tax

Let both systems execute one identical Decode Turn of cost `D`, with no switch.
Their useful Turn capacities are

$$
\mu_{AX}=\frac{1}{D+h_{AX}},
\qquad
\mu_{TV}=\frac{1}{D+h_{TV}}.
$$

The exact capacity ratio in this reduced model is

$$
R_0=\frac{\mu_{TV}}{\mu_{AX}}
   =\frac{D+h_{AX}}{D+h_{TV}}.
$$

If AX overhead is the reference zero and TurnVector adds `delta * D`, then

$$
R_0=\frac{1}{1+\delta}.
$$

| TurnVector extra decision cost | Exact analytic ratio |
| ---: | ---: |
| `1% of D` | `0.9901` |
| `5% of D` | `0.9524` |
| `10% of D` | `0.9091` |
| `25% of D` | `0.8000` |

These values are arithmetic consequences of the assumption, not observations.

### S1: equal-cost Decode competition

For `m` equal models, one common Decode Turn costs `D`, and every cyclic
transition costs `S`. If all models remain runnable,

$$
C_P=m(D+S+h_P),
$$

and aggregate useful Turn throughput is

$$
\mu_P=\frac{1}{D+S+h_P}.
$$

Therefore

$$
R_1=\frac{D+S+h_{AX}}{D+S+h_{TV}}.
$$

At zero switch cost and equal overhead, `R_1=1` exactly. Increasing switch
cost pushes the ratio toward 1 because the shared switch cost dominates the
overhead difference; it does not by itself favor either scheduler.

For one visible token per Turn, the saturated stream gap is one full cycle:

$$
G_P=m(D+S+h_P).
$$

Thus a necessary saturated-state condition for Stream SLO `L_S` is

$$
m \le \left\lfloor\frac{L_S}{D+S+h_P}\right\rfloor.
$$

For B4, replace `D` with the common B4 cost and count the common useful output
per Turn. If both schedulers receive the same B4 candidate, this substitution
does not create a scheduler-only throughput advantage.

### S2: long Prefill against Interactive Decode

For one background Prefill chunk and one interactive Decode Turn in an
exclusive alternating cycle,

$$
G_P(q,x)=P(q,x)+D+2S+2h_P.
$$

The interactive stream-gap condition is

$$
G_P(q,x) \le L_S.
$$

Define the largest safe chunk at position `x` as

$$
q^{crit}_P(x)=\max\{q\in Q:\ P^+_P(q,x)+D+2S+2h_P\le L_S\}.
$$

Then:

- AX-F is SLO-feasible at `x` only if `64 <= q^crit_AX(x)` while a sibling is
  active;
- AX-A is SLO-feasible only if every lagged controller output is no greater
  than the corresponding `q^crit_AX(x)`;
- TurnVector's ideal envelope chooses `q^crit_TV(x)` directly from current
  slack and conservative cost;
- TurnVector's conservative envelope may choose a smaller chunk because it
  also caps the chunk by a fixed Decode-Turn service target.

No numeric S2 capacity ratio follows without the actual `P(q,x)` surface,
legal chunk set, overheads, switch cost, and SLO.

### S3: heterogeneous service fairness

Let two models receive equal request counts and require one Turn per request,
with costs `c_1:c_2 = 1:r`. Their offered Engine Service shares are

$$
d_1=\frac{1}{1+r},\qquad d_2=\frac{r}{1+r}.
$$

AX equal-turn rotation produces the same shares, so its normalized
service-demand capacity is 1 before common overhead.

Let TurnVector weights be `w_1:w_2 = 1:a`. Its configured shares are
`1/(1+a)` and `a/(1+a)`. The normalized demand scale it can sustain is

$$
\rho^*_{TV}
=\min\left(
\frac{1+r}{1+a},
\frac{a(1+r)}{r(1+a)}
\right).
$$

| Cost ratio `1:r` | Weight ratio `1:a` | Exact normalized TV capacity | Bottleneck |
| ---: | ---: | ---: | --- |
| `1:2` | `1:1` | `0.7500` | expensive model |
| `1:2` | `1:3` | `0.7500` | cheap model |
| `1:4` | `1:1` | `0.6250` | expensive model |
| `1:4` | `1:3` | `0.9375` | expensive model |

The critical weight is `a=r`. At that point configured share matches offered
Engine Service demand and the normalized value reaches 1. This is an important
negative result: weighted fairness is a policy, not an automatic throughput
improvement. A deliberately mismatched weight can reduce request goodput while
improving the intended differentiated service share.

## What V1 proves

V1 establishes only these analytic facts:

1. With identical work, switching, and overhead, pure scheduling cannot improve
   raw throughput.
2. Fresh-plan overhead creates a directly calculable one-model tax.
3. Equal-turn rotation gives service share in proportion to Turn cost, while
   TurnVector targets configured weight share.
4. A Prefill/Decode SLO comparison is determined by the true position-dependent
   Prefill cost and the largest chunk that fits remaining slack.
5. There is no universal `lambda*_TV / lambda*_AX` without arrival burst,
   request-work, Cost Surface, and SLO inputs.

V1 does not establish TTFT, TPOT, goodput, memory, GPU occupancy, or product
capacity for either implementation.

## Inputs required for a numeric result

A future benchmark or calibrated analytic instantiation must bind:

- `D_i(model,batch,kv)` and `P_i(model,position,chunk)` distributions;
- switch-cost distribution and its exact attribution boundary;
- AX and TurnVector decision/replan overhead distributions;
- output length and Prefill length per request class;
- arrival mix, burst period/size, and phase alignment;
- First Token, Stream, and Progress obligations;
- model weights and service classes;
- legal common chunk set and active AX controller branch;
- Cost Profile error distribution, not only one percentage;
- exact code, model, dependency, hardware, and OS identities.

The next version turns these symbols into explicit feasible sets, lower and
upper capacity bounds, and critical-state equations.

## Future benchmark matrix

The original comparison brief remains the intended measurement matrix. Listing
it here does not create observations.

| Dimension | Values |
| --- | --- |
| Offered load | `rho=0.10..1.30` by `0.05`; refine the capacity boundary to `0.005`. |
| Arrival shape | Evenly spaced; periodic burst with the same mean, 25% duty, and 4x ON-rate. The burst period must also be fixed. |
| Switch cost | `0`, `0.1`, `0.5`, `1`, `2` isolated B1 Decode Turns. |
| TV extra decision cost | `0%`, `1%`, `5%`, `10%`, `25%` of one Decode Turn. |
| Cost Profile error | `0`, `+/-10%`, `+/-25%`, `+/-50%`. |
| First Token SLO | `1.5x`, `2x`, `4x`, `8x` isolated first-token critical path. |
| Stream SLO | `1.5x`, `2x`, `4x`, `8x` isolated Decode Turn. |
| Progress Bound | `2x`, `4x`, `8x`, `16x` next isolated Turn. |
| Request success threshold | Primary `99%`; sensitivity `95%`. |
| S1 sensitivity | 2-model primary; 4/8 models and B4. |
| S3 sensitivity | Cost ratios `1:2`, `1:4`; weights `1:1`, `1:3`. |
| AX policy | Fixed `64/256` and adaptive `64 -> 40 ms`, clamp `1..256`, grace `250 ms`. |
| TV policy | Conservative fixed service target and ideal Latest-Safe-Start envelope. |

## Evidence anchors

- AX exclusive arbiter and cyclic waiter selection:
  [`service.rs`](https://github.com/defai-digital/ax-engine/blob/16c28557edf2b8469fe7f247505ae038d54472c7/crates/ax-engine-server/src/generation/service.rs#L40-L347)
- AX Prefill feedback controller:
  [`service.rs`](https://github.com/defai-digital/ax-engine/blob/16c28557edf2b8469fe7f247505ae038d54472c7/crates/ax-engine-server/src/generation/service.rs#L355-L460)
- TurnVector bounded Turns:
  [`ADR 0001`](https://github.com/token-plant/TurnVector/blob/35d9109d4e3f9686c5b9acfd5324f5bb31bf003c/docs/adr/0001-interleave-bounded-device-turns-for-the-mvp.md)
- TurnVector weighted Engine Service:
  [`ADR 0003`](https://github.com/token-plant/TurnVector/blob/35d9109d4e3f9686c5b9acfd5324f5bb31bf003c/docs/adr/0003-use-runnable-only-weighted-engine-service-fairness.md)
- TurnVector Latest Safe Start and Urgent Set:
  [`ADR 0015`](https://github.com/token-plant/TurnVector/blob/35d9109d4e3f9686c5b9acfd5324f5bb31bf003c/docs/adr/0015-use-deterministic-deadline-aware-weighted-service-selection.md)
- Benchmark evidence/claim separation: [Performance Publication](PERFORMANCE-PUBLICATION.md)
