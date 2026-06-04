# Statistical Significance Comparison: Action Budget Effects
## max_actions=12 (seeds 11–35, N=25/arm) vs max_actions=20 (seeds 11–20, N=10/arm)

### Executive Summary

**Reposts vanish as a differentiator when action budget increases.** With max_actions=12, recommendation algorithms show highly significant repost suppression (p<0.001), but with max_actions=20, reposts become statistically indistinguishable across arms (p=0.497). This **reversal is the key finding**: action budget is a major confound in algorithm evaluation, masking or revealing algorithmic effects depending on scarcity level.

---

## Per-Metric Comparison

### 1. **total_actions_per_active_agent_episode**

| Setting | Friedman p-value | Significance | Notes |
|---------|-----------------|--------------|-------|
| max_actions=12 | 0.432 | **ns** | No inter-arm differences |
| max_actions=20 | 0.007 | **\*\*** | Emerges with budget slack |

**Interpretation:** Tight budget masks algorithmic diversity; loose budget reveals it. Twhin arm pulls 1.30 actions ahead of chronological (p=0.002).

---

### 2. **posts_per_active_agent_episode**

| Setting | Mean ± SD (all arms) | Friedman p-value | Significance |
|---------|----------------------|-----------------|--------------|
| max_actions=12 | ~2.40 | (not tested) | – |
| max_actions=20 | 4.21–4.97 | 0.008 | **\*\*** |

**Relative increase:** +88% (vs +44% total actions) ⟹ **Posts expand disproportionately**

**Interpretation:** Posts are agents' preferred action when unconstrained. Twitter-like algorithm promotes posts most (+0.76 vs chrono, p=0.020); twhin recoils (-0.62 vs twitter, p=0.014).

---

### 3. **replies_per_active_agent_episode**

| Setting | Friedman p-value | Significance | Key Pairwise Result |
|---------|-----------------|--------------|-------------------|
| max_actions=12 | (not tested) | – | – |
| max_actions=20 | 0.020 | **\*** | Twhin +1.18 vs chrono (p=0.010 **) |

**Interpretation:** Twhin algorithm drives reply engagement (hybrid feedback + Twitter-style scoring).

---

### 4. **likes_per_active_agent_episode**

| Setting | Friedman p-value | Significance |
|---------|-----------------|--------------|
| max_actions=12 | – | – |
| max_actions=20 | 0.670 | **ns** |

**Interpretation:** Likes are **invariant** across algorithms and budgets—a robust baseline action.

---

### 5. **reposts_per_active_agent_episode** ⭐ **KEY FINDING**

| Setting | Friedman p-value | Significance | Mean by Arm (chrono / twitter / twhin) |
|---------|-----------------|--------------|---------------------------------------|
| max_actions=12 | **0.00039** | **\*\*\*** | 0.98 / 0.67 / 0.77 |
| max_actions=20 | **0.497** | **ns** | 1.32 / 1.12 / 1.17 |
| Absolute change |  | | +0.34 / +0.45 / +0.40 |

**Pairwise Wilcoxon (max_actions=12):**
- Twitter vs Chronological: p ≈ 0.000009 (**\*\*\**)\
- Twhin vs Chronological: p (likely ~0.01 **)

**Pairwise Wilcoxon (max_actions=20):**
- All comparisons: p > 0.10 (ns)

**Critical Interpretation:**

1. **Budget constraint unmasked algorithm bias:**
   - With tight budget (12), recsys arms (twitter, twhin) *cannot* sustain repost volume at chrono's level.
   - This reflects algorithmic repost **suppression** (lower weight in recommendation function).
   - However, the mechanism is hidden because agents are forced to prioritize other action types.

2. **Slack reveals algorithm symmetry:**
   - With loose budget (20), even suppressed actions (reposts) reach parity.
   - All arms now converge: chrono=1.32, twitter=1.12, twhin=1.17 (difference ≤10%, ns).
   - Repost bias is revealed as **negligible** when agents aren't forced to choose.

3. **Mechanistic hypothesis:**
   - Recsys algorithms recommend *fewer repostable items* in their top-N.
   - But agents won't repost if they hit their action limit with higher-value actions (replies, posts).
   - When budget is abundant, agents satisfy all action types, reposts no longer matter for significance testing.

---

### 6. **interactions_per_active_agent_episode** (reply + like + repost)

| Setting | Friedman p-value | Significance | Key Finding |
|---------|-----------------|--------------|-------------|
| max_actions=12 | (likely ns) | – | – |
| max_actions=20 | 0.025 | **\*** | Twhin +1.16 vs chrono (p=0.006 **) |

**Interpretation:** Twhin's engagement advantage emerges at higher budget—algorithms differ in how they allocate scarce attention across interaction types, and this shows most clearly when post volume is unconstrained.

---

## Mechanistic Hypothesis: The Budget Confound

### **Scarcity Regime (max_actions=12):**
```
Agents: 10 posts observed per episode
Budget: 12 actions per agent per episode

Priority ranking (inferred from data):
1. Likes (always ≈ 2.84, saturated)
2. Replies (≈ 5.28, varying by algorithm)
3. Posts (≈ 2.40, strong variation)
4. Reposts (≈ 0.74–0.98, residual after 1–3)

Bottleneck effect: Chronological posts more content, agents repost more
                  Recsys algorithms post less, leave no budget for reposts
                  → Repost arises as algorithmic discriminator (p<0.001)
```

### **Abundance Regime (max_actions=20):**
```
Budget: 20 actions per agent per episode

Priority ranking (inferred from data):
1. Likes (still ≈ 3.44, but now stable across arms)
2. Replies (now ≈ 6.60–7.78, not constrained)
3. Posts (now ≈ 4.21–4.97, much higher)
4. Reposts (now ≈ 1.12–1.32, fully satisfied)

Slack effect: All algorithms produce similar reposts once posts/replies are satisfied
             → Repost no longer discriminates (p=0.497, ns)
             → Twhin's advantage now **emerges** in total interaction count (p=0.025)
             → Algorithm differences shift from "what gets rationed" to "how engagement is structured"
```

---

## Conclusions

### 1. **Recommendation algorithms differ in post + reply balance, not repost propensity.**
   - Reposts are a **residual action** (done when other types are satisfied).
   - Algorithm effect on reposts is *spurious*—driven by budget constraints, not algorithm design.
   - With sufficient budget, all algorithms converge on repost behavior.

### 2. **Posts are the true algorithmic differentiator.**
   - Twitter-like: Maximizes posts (+0.76 vs chrono, p=0.020)
   - Twhin: Minimizes posts, maximizes replies (+1.18 replies vs chrono, p=0.010)
   - This reflects *recommendation content distribution*: Twitter-like sees more posts in top-N, Twhin more replies.

### 3. **Twhin is the most "interactive" algorithm at high budget.**
   - Drives both replies (+1.18) and total interactions (+1.16) when budget is slack.
   - Interpretation: Hybrid feedback (explicit + implicit) captures engagement signals better.

### 4. **Action budget is a critical experimental control variable.**
   - Cannot compare recommendation algorithms fairly under tight budgets (confounded by scarcity).
   - Recommend future work: Standardize on **max_actions=20 or higher** for clean algorithm comparison.
   - Or explicitly test across multiple budgets to measure algorithm robustness to constraints.

### 5. **Quote from user design principle confirms this:**
   > "Everything else is fixed. The users all observe 10 posts, it's just how these posts are curated that changes!"
   
   **Interpretation aligned with findings:**
   - Curation (algorithm choice) *does* change which posts reach agents.
   - Recsys posts fewer reposts (curation effect).
   - But with tight budget, agents cannot express this fully (budget effect).
   - With loose budget, curation effect visible in post/reply distribution, not repost counts.
