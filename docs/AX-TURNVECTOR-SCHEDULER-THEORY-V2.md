# AX Engine vs TurnVector Pure Scheduling Theory V2

## Status and result

This document extends [V1](AX-TURNVECTOR-SCHEDULER-THEORY-V1.md) from baseline
equations to a parameterized feasible region, lower and upper bounds, and
critical states. It is still analytic evidence, not a measured benchmark or a
simulation.

For an SLO success threshold `alpha` and a one-dimensional workload scale
`lambda`, the only defensible general result is an interval:

$$
\boxed{
\frac{\lambda^{-,*}_{TV,\alpha}}
     {\lambda^{+,*}_{AX,\alpha}}
\le
\frac{\lambda^*_{TV,\alpha}}
     {\lambda^*_{AX,\alpha}}
\le
\frac{\lambda^{+,*}_{TV,\alpha}}
     {\lambda^{-,*}_{AX,\alpha}}
}
$$

Here `-` is a conservative sufficient bound and `+` is an optimistic necessary
bound. The displayed ratio interval requires positive denominator bounds; if an
AX lower bound is zero, the corresponding upper ratio is unbounded rather than
a finite result. An exact point is justified only when the lower and upper
boundaries coincide after real cost and arrival inputs are bound.

## Parameter domain

Let the complete theory input be

$$
\theta=(p,\lambda,\rho,\alpha,b,T_b,C,S,h,Q,\epsilon,w,L_F,L_S,L_P).
$$

| Input | Meaning |
| --- | --- |
| `p_r` | Fraction of offered requests in request class `r`; `sum p_r = 1`. |
| `lambda_r=lambda*p_r` | Arrival rate for class `r`. |
| `rho=lambda*sum(p_r*e_r)` | Common useful Engine Service load before scheduler and switch overhead. |
| `alpha` | Required all-applicable-SLO success fraction, normally `0.99` or `0.95`. |
| `b_r` | Arrival-envelope burst allowance for class `r`. |
| `T_b` | ON/OFF burst period; required to turn 25% duty into a finite burst size. |
| `C` | True Engine Service Cost Surface and per-request Turn closure. |
| `S` | Model-switch cost matrix. |
| `h_P` | Scheduler `P` overhead per decision/replan. |
| `Q` | Common finite chunk set. |
| `epsilon` | Cost-prediction error bound or quantile. |
| `w_i` | TurnVector model weights. |
| `L_F`, `L_S`, `L_P` | First-token, stream-gap, and progress obligations. |

The feasible region is not a function of mean load alone. A 25% duty, 4x
ON-rate burst also needs its period `T_b`. A valid leaky-bucket upper envelope
for that periodic fluid source has burst term

$$
b^{burst}_r=\frac{3}{4}\lambda_r T_b.
$$

For discrete requests, add the chosen boundary-arrival convention, normally at
most one request. Without `T_b`, burst backlog and TTFT cannot have a finite
numeric analytic bound.

## Per-request occupied-time bounds

For scheduler `P` in `{AX,TV}`, let request class `r` require true Engine
Service `e_r`, `n_r` bounded Turns, `d_{r,P}` scheduling decisions or fresh
replans, and `k_{r,P}` model switches. A rejected or stale Plan can increase
`d` without increasing completed Turns. Bind scheduler overhead and counts by

$$
h^-_P\le h_P\le h^+_P,
\qquad
d^-_{r,P}\le d_{r,P}\le d^+_{r,P},
\qquad
k^-_{r,P}\le k_{r,P}\le k^+_{r,P}.
$$

Then the optimistic and conservative occupied times are

$$
W^-_{r,P}=e_r+d^-_{r,P}h^-_P+k^-_{r,P}S,
$$

$$
W^+_{r,P}=e_r+d^+_{r,P}h^+_P+k^+_{r,P}S.
$$

For a work-conserving exclusive server, raw capacity is bounded by

$$
\lambda^{-,*}_{raw,P}
=\frac{1}{\sum_r p_r W^+_{r,P}},
\qquad
\lambda^{+,*}_{raw,P}
=\frac{1}{\sum_r p_r W^-_{r,P}}.
$$

The lower expression assumes the worst allowed overhead and switch count. The
upper expression assumes the best allowed values. These are raw-work bounds;
SLO feasibility may reduce capacity further.

## Deadline-demand feasible region

Represent each request as a finite set of scheduling obligations `o`: first
token, each next-token gap, and Standard/Background progress. Each obligation
has relative deadline `L_o` and conservative/optimistic complete occupied cost
`C^+_{o,P}` / `C^-_{o,P}`.

Let `dbf_{o,alpha}(t;lambda)` be a demand-bound function: the largest number of
class-`o` obligations, under the selected deterministic or probabilistic
arrival envelope, whose release and deadline both fit an interval of length
`t`. For a smooth sporadic stream with minimum spacing `T_o`, no release jitter,
and one possible release at the interval boundary,

$$
dbf_o(t)=
\begin{cases}
0, & t<L_o,\\
1+\left\lfloor\dfrac{t-L_o}{T_o}\right\rfloor, & t\ge L_o.
\end{cases}
$$

A burst envelope replaces the leading `1` with its finite burst allowance or
uses a tighter trace-specific demand bound.

Let

$$
B^+_P=\max_k C^+_{k,P}
$$

be the largest non-preemptive Turn that may already be in flight when an
obligation becomes ready. Define:

$$
\mathcal T_{\alpha}(\lambda)
=\{t>0:\sum_o dbf_{o,\alpha}(t;\lambda)>0\}
$$

as the critical interval lengths at which at least one obligation can be due.
For finite sporadic task sets it is sufficient to check their deadline
endpoints within the bounded busy period; retaining the set notation avoids
assuming a task period that has not yet been supplied.

$$
\mathcal F^-_{P,\alpha}
=\left\{\theta:
\sum_r\lambda_rW^+_{r,P}<1
\ \land\
B^+_P+\sum_o dbf_{o,\alpha}(t;\lambda)C^+_{o,P}\le t
\quad\forall t\in\mathcal T_{\alpha}(\lambda)
\right\},
$$

$$
\mathcal F^+_{P,\alpha}
=\left\{\theta:
\sum_r\lambda_rW^-_{r,P}\le1
\ \land\
\sum_o dbf_{o,\alpha}(t;\lambda)C^-_{o,P}\le t
\quad\forall t\in\mathcal T_{\alpha}(\lambda)
\right\}.
$$

The first set is deliberately conservative: it charges the largest possible
non-preemptive blocking Turn plus upper costs. The second is only necessary:
even an ideal ordering cannot execute more due work than the interval contains.
Therefore

$$
\mathcal F^-_{P,\alpha}\subseteq
\mathcal F_{P,\alpha}\subseteq
\mathcal F^+_{P,\alpha}.
$$

For a workload ray `lambda_r=lambda*p_r`, define

$$
\lambda^{-,*}_{P,\alpha}
=\sup\{\lambda:\theta(\lambda)\in\mathcal F^-_{P,\alpha}\},
$$

$$
\lambda^{+,*}_{P,\alpha}
=\sup\{\lambda:\theta(\lambda)\in\mathcal F^+_{P,\alpha}\}.
$$

These definitions produce the capacity-ratio interval at the start of this
document.

## Meaning of 99% and 95%

A deterministic smooth or fixed burst trace either satisfies the analytic
guarantee or does not; inside the sufficient region the guarantee is 100%, so
both 99% and 95% pass.

To obtain distinct 99% and 95% analytic boundaries, bind probabilistic arrival
and cost envelopes. For example, choose per-class failure budgets `eta_o` with

$$
\sum_o\eta_o\le1-\alpha,
$$

then use `b_{o,1-eta_o}` and `C^+_{o,1-eta_o}` in the sufficient test. The
current parameter grid supplies percentages but no probability law, so it
cannot produce different numeric 99% and 95% boundaries by mathematics alone.

## AX exclusive feasible region

With all `m` models continuously runnable, one representative Turn per visit,
and cyclic switch costs, AX has exact cycle

$$
C_{AX}=\sum_i(c_i+h_{AX})+\sum_iS_{i,next(i)}.
$$

Let `a_i` be offered useful Engine Service per unit time for model `i`. Since AX
completes one representative Turn per cycle, its saturated per-model stability
condition is

$$
a_i<\frac{c_i}{C_{AX}}.
$$

For one visible token per representative Turn, necessary cyclic SLO conditions
are

$$
C_{AX}\le L_{S,i}
\quad\text{and}\quad
C_{AX}\le L_{P,i}
$$

for each continuously runnable model to which those obligations apply. If an
interactive first token requires `n_i^F` visits after admission, a conservative
round-robin bound is

$$
TTFT_i\le n_i^F C_{AX}.
$$

This becomes a critical state when any inequality is an equality.

## TurnVector weighted feasible region

Let

$$
\eta_{TV}=1-\text{fraction of time consumed by switch and scheduling overhead}.
$$

For continuously runnable models, the long-run weighted-service stability
condition is

$$
a_i<\eta_{TV}\frac{w_i}{\sum_jw_j}.
$$

The non-urgent ledger realizes the weighted share; Latest Safe Start adds
deadline order. Since urgent service is still charged, urgency can remain
feasible only if the total deadline demand fits the demand-bound region above.
It cannot repair overload.

At state `x`, let

$$
\Delta(x)=\min_{j\ne i}\{LatestSafeStart_j-now\}
$$

be the earliest sibling slack before a proposed Prefill Turn. With Cost Profile
error convention

$$
(1-\epsilon)c\le\hat c\le(1+\epsilon)c,
$$

use

$$
c^+(q,x)=\frac{\hat c(q,x)}{1-\epsilon}.
$$

The ideal safe chunk envelope is

$$
q^+_{TV}(x)=
\max\{q\in Q:\ c^+(q,x)+h^+_{TV}+S^+\le\Delta(x)\}.
$$

For fixed target `T_D`, the conservative envelope is

$$
q^-_{TV}(x)=
\max\{q\in Q:\ c^+(q,x)\le T_D
\ \land\
c^+(q,x)+h^+_{TV}+S^+\le\Delta(x)\}.
$$

If either set is empty, the Prefill candidate is not safe to start before the
urgent sibling. The scheduler must choose the sibling or report infeasibility;
it may not invent a smaller unqualified chunk.

## Scenario boundaries

### S0: one model

The exact reduced-model ratio remains

$$
R_0=\frac{D+h_{AX}}{D+h_{TV}}.
$$

Critical states:

- raw saturation: `lambda * request_work_P = 1`;
- per-Turn SLO: `D+h_P=L_S`;
- scheduler parity: `h_TV=h_AX`, where `R_0=1`.

### S1: equal-cost Decode competition

For `m` saturated equal models,

$$
R_1=\frac{D+S+h_{AX}}{D+S+h_{TV}},
\qquad
G_P=m(D+S+h_P).
$$

Critical model count:

$$
m^{crit}_P=\left\lfloor\frac{L_S}{D+S+h_P}\right\rfloor.
$$

The next model beyond this value cannot join the continuously backlogged state
while preserving the same stream SLO. With zero switch and equal overhead,
raw throughput is exactly equal for 2, 4, or 8 models and for any common Batch
candidate.

### S2: Prefill interference

For one Prefill and one Decode Turn per alternating cycle,

$$
G_P(q,x)=P(q,x)+D+2S+2h_P.
$$

The critical chunk is

$$
q^{crit}_P(x)=
\max\{q\in Q:G^+_P(q,x)\le L_S\}.
$$

AX-F crosses the SLO boundary when

$$
G^+_{AX}(64,x)=L_S.
$$

AX-A crosses controller states at:

- previous runner time `t_k=40000 us`: blend versus immediate snap;
- estimated `u_k<=156 us/token`: target clamps at 256;
- estimated `u_k>=20001 us/token`: target clamps at 1;
- sibling activity age `250 ms`: contended versus idle state;
- controller output `q_{k+1}=q^crit_AX(x)`: the next larger quantum violates
  the analytic stream bound.

Because AX-A uses the previous chunk's cost, a proof also needs a bound on
position-to-position cost growth. Without such a bound, an abrupt increase in
`P(q,x)` can overshoot even when the previous chunk met 40 ms.

For TurnVector, a chunk `q` becomes unsafe at Cost Profile error

$$
\epsilon^{crit}(q,x)
=1-\frac{\hat c(q,x)}{\Delta(x)-h^+_{TV}-S^+},
$$

provided the denominator is positive. At equality the chunk lies exactly on
the Latest Safe Start boundary. The value is meaningful as an error threshold
only when it lies in `[0,1)`: a negative value means the chunk is already
unsafe at zero error. A larger valid error bound forces the next smaller
qualified chunk or makes Prefill temporarily infeasible.

### S3: heterogeneous fairness

For equal request counts, cost ratio `1:r`, and TurnVector weight ratio `1:a`,

$$
\rho^*_{TV}
=\min\left(\frac{1+r}{1+a},
            \frac{a(1+r)}{r(1+a)}\right).
$$

The critical weight is

$$
a^{crit}=r.
$$

If `a<r`, the expensive model is the bottleneck. If `a>r`, the cheap model is
the bottleneck. At `a=r`, configured weighted share matches offered Engine
Service demand and the normalized value is 1 before scheduler/switch overhead.

For arbitrary demand shares `d_i`, the general weighted limit is

$$
\rho^*_{TV}=\min_i\frac{w_i/\sum_jw_j}{d_i}.
$$

AX's equal-turn share is `c_i/sum(c_j)` in this saturated reduced model. Its
weighted-service error against TurnVector's configured target can be reported
as

$$
E_{AX}=\max_i\left|\frac{c_i}{\sum_jc_j}
-\frac{w_i}{\sum_jw_j}\right|.
$$

## Critical-state registry

| Critical state | Equality | Meaning immediately beyond it |
| --- | --- | --- |
| Raw saturation | `sum lambda_r W_r = 1` | Queue is no longer stable. |
| Deadline demand | `B + sum dbf_o(t) C_o = t` | A further arrival/cost increase loses the sufficient SLO guarantee. |
| Necessary demand | `sum dbf_o(t) C_o = t` | More due work cannot fit even with ideal ordering. |
| TurnVector urgency | `now = LatestSafeStart` | Candidate enters the Urgent Set. |
| TurnVector chunk edge | `occupied_cost(q)=Delta` | Next larger chunk carries a sibling beyond safe start. |
| AX cycle gap | `C_AX=L_S` | Saturated next-token gap is exactly at the Stream SLO. |
| AX fixed Prefill edge | `G_AX(64,x)=L_S` | Fixed contested quantum stops being SLO-feasible. |
| AX adaptive clamp | `u=156` or `u=20001 us/token` | Target enters max- or min-quantum clamp. |
| AX adaptive mode | `last_runner=40000 us` | Controller changes from blend to snap. |
| Sibling grace | `age=250 ms` | AX activity state changes. |
| Fair-share match | `w_i/sum(w)=d_i` for every `i` | No model is over- or under-provisioned by weight. |
| Scheduler parity | `h_TV=h_AX` under identical work/switches | Pure overhead ratio is 1. |
| Cost-error edge | `epsilon=epsilon^crit(q,x)` | A formerly safe Turn must shrink or defer. |

## Mapping the theory to a future benchmark

The future benchmark should preserve this theory as a pre-run contract and
replace symbols only with evidence that has the correct identity and unit.

| Theory input | Required evidence |
| --- | --- |
| `D_i(b,k)` | Raw per-Turn Decode Engine Service samples by exact model, batch, and KV bucket. |
| `P_i(q,x)` | Raw position/chunk Prefill Engine Service samples, including tails. |
| `S_ij` | Paired same-model versus cross-model transition samples with an explicit attribution boundary. |
| `h_AX`, `h_TV` | Direct decision/replan samples outside Engine Service; no transport or GPU time relabeling. |
| `epsilon` | Cost prediction residual distribution, tied to the exact Cost Profile identity. |
| `b`, `T_b` | Hash-bound arrival trace and explicit burst period/phase. |
| `L_F`, `L_S`, `L_P` | Frozen pre-run SLO contract. |
| `w_i` | Exact configured Model Weights and changes. |
| `q` / controller state | Per-Turn chosen chunk, previous cost input, sibling-active state, and active branch. |
| `lambda*` | Monotone load sweep plus bounded refinement; late requests continue to completion. |

Required reported outputs remain:

- offered load, SLO goodput, and raw throughput;
- TTFT p95/p99, TPOT p95/p99, and Progress p99;
- per-model Engine Service share and weighted-service error;
- switch, Turn, decision, stale-plan, and fresh-replan counts;
- completed total work and output identity;
- exact AX, TurnVector, Benchmark, model, dependency, hardware, OS, trace,
  Cost Profile, and SLO identities.

## Acceptance invariants

1. S0 changes only scheduler overhead.
2. S1 at zero switch and equal overhead has identical raw throughput.
3. Relaxing an SLO cannot reduce theoretical or measured goodput.
4. Both schedulers receive the same arrivals, candidate batches, true costs,
   and output work.
5. Late requests finish so completed total work can be compared exactly.
6. An analytic row is labeled `analytic`; a simulated row is labeled
   `simulation`; an implementation run is labeled `measurement`.
7. No analytic or fixture row is promoted as a product benchmark result.

## Current unresolved implementation boundary

At the pinned TurnVector design baseline, the implementation plan still lists
deadline-aware Turn selection, Release scheduler measurement, and Benchmark
policy/performance adapters as future rows (`C37`, `C45`, `Q03`, and `Q04`).
Consequently V2 supplies parameterized bounds only. It does not assign a
single measured TurnVector capacity or claim that the current implementation
is at its eventual optimum.
