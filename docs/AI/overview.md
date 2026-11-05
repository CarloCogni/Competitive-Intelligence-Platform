# How the AI Works

## Why AI?

The dashboard tracks dozens of organizations across multiple data sources: GitHub activity, news coverage, app store ratings, social media mentions. Every few hours, new data arrives. Across 40+ entities and 16 metric types, the volume of raw signals quickly becomes unmanageable. No one has time to cross-reference all of it manually and figure out what actually matters.

The AI systems handle five problems that humans can't efficiently solve at this scale:

1. **Knowing when something is unusual** — not by a fixed rule, but relative to each entity's own history.
2. **Recognizing meaningful combinations** — patterns where multiple signals move together in ways that historically mattered.
3. **Summarizing the big picture** — turning a week of raw data into a concise briefing a human can read in two minutes.
4. **Spotting new competitors** — automatically surfacing untracked companies and products that appear in the same news as your tracked entities.
5. **Measuring competitive momentum** — aggregating all metric changes into a single "speed" score per entity, tracking who is gaining ground and who is losing it.

Each of these is a separate system. They work together but serve different purposes.

---

## 1. Smart Alerts: Is This Change Unusual?

The dashboard monitors metrics like GitHub stars, news mentions, and app store ratings. When a metric changes significantly, the system fires an alert. But "significant" means different things for different entities.

A 20% jump in GitHub stars is routine noise for a small project with 500 stars — that's just 100 new stars, which can happen from a single social media post. The same 20% increase for a project with 50,000 stars means 10,000 new stars in one period — that's a major event.

A fixed threshold (e.g., "alert me when anything changes more than 20%") would flood you with false alarms for small, volatile entities and miss real events for large, stable ones.

### How the system adapts

Instead of using the same threshold for everyone, the system learns what "normal" looks like for each entity individually. It studies the historical pattern of changes for each metric of each entity — how much does this specific metric typically fluctuate for this specific organization? — and builds a statistical profile.

When new data arrives, the system compares the current change against that entity's own historical pattern. A change that would be unremarkable for a volatile startup might trigger an alert for a stable enterprise project, and vice versa.

For new entities that don't have enough history yet, the system uses a general-purpose AI to estimate a reasonable starting threshold based on the entity's characteristics (size, category, available data). As history accumulates, the statistical model takes over.

**The question this system answers:** *"Is this change unusual for this specific entity, given its own history?"*

---

## 2. Compound Alerts: Do These Signals Mean Something Together?

Single-metric alerts catch individual changes. But some of the most important competitive signals only become visible when you look at multiple metrics together.

A 10% increase in GitHub stars, by itself, is not noteworthy. A 20% increase in news mentions, by itself, might just be a slow news day. But when stars, news mentions, and app store activity all move at the same time for the same entity — that's a pattern. Something real is happening: a product launch, a viral moment, a strategic shift.

The problem is that there are too many possible combinations to define these rules by hand. With 16 metric types, each with a range of possible thresholds and two directions (increase or decrease), the number of possible multi-metric rules is enormous.

### How the system discovers patterns

The system uses an evolutionary approach — inspired by natural selection — to automatically discover which combinations of metric changes correspond to events that actually mattered.

It starts with a large population of random candidate rules (e.g., "stars up 15% AND news up 80%", "app rating down 5% AND open issues up 30%"). Each rule is tested against historical data to see how well it separates useful alerts from noise. Rules that perform well survive and combine with other successful rules. Rules that perform poorly are discarded. Over many generations of this process, the system converges on a small set of high-quality compound rules.

**The question this system answers:** *"Did a combination of metrics move together in a way that historically meant something important?"*

### How your behavior trains the system

This is where it gets interesting: **the system learns from how you use it.**

When you see an alert in the dashboard and dismiss it (click the X), you're implicitly telling the system: *"This was useful — I looked at it and acted on it."* When you see an alert and ignore it for weeks, you're implicitly telling the system: *"This was noise — not worth my attention."*

The compound rule engine uses this feedback as its training signal. It looks at which combinations of metric changes were present when you dismissed alerts (positive signal) versus when you ignored them (noise), and evolves rules that match the patterns you actually care about.

Think of it like a spam filter that learns from what you mark as important. Over time, the rules get better at surfacing the multi-metric patterns that matter to your team and filtering out the ones that don't. The more you interact with the alert system, the smarter the compound rules become.

This learning cycle runs monthly. Each month, the system re-evaluates its rules using the latest dismissal history, potentially discovering new patterns or retiring rules that no longer perform well.

---

## 3. Intelligence Briefings: What Happened This Week?

Even with smart alerts and compound detection, the dashboard still produces a stream of individual signals. For a busy strategy or product team, the question is often not "show me every alert" but "give me the summary."

The briefing system solves this. Once a week (or on demand), it feeds all recent competitive signals — alerts, metric changes, and news headlines — to a large language model (AI) and asks it to produce a structured briefing:

- **Executive Summary** — 2-3 sentences on the overall competitive landscape this week.
- **Key Threats** — Competitor movements that need immediate attention.
- **Opportunities** — Competitor weaknesses, slowdowns, or market gaps you could exploit.
- **Watch List** — Entities showing unusual activity worth monitoring next week.

The result is a concise, readable document that distills a week of raw data into actionable intelligence. Instead of scanning dozens of alerts and news items, you read one briefing.

Briefings are delivered in the dashboard and optionally by email, so your team can stay informed without logging in.

---

## 4. Competitor Detection: Who's New on the Scene?

The dashboard tracks a fixed list of competitors loaded from configuration. But new companies, wallets, and protocols emerge constantly. Scanning hundreds of news articles manually for untracked names does not scale.

The competitor detection system automates this. Every time new articles are fetched (every 2 hours), the system scrapes article bodies for full content, then sends batches of titles and content directly to an LLM. The LLM — the same Ollama/Groq backend used by the other AI systems — analyzes the articles and extracts competitor entities with structured metadata: a category (competitor, exchange, wallet, protocol, etc.), a relevance score (1-10), and a brief explanation of why the entity matters competitively.

### How the LLM extracts competitors

Unlike a simpler approach that would extract names with regex and then filter them, this system skips regex entirely. The LLM sees the full article context — title and body — so it understands that "Bitcoin Firm Blockstream" is about Blockstream (already tracked), that "Financial Times" is a newspaper (not a competitor), and that "Your Ledger With" is a sentence fragment (not an entity at all).

The LLM receives the complete list of currently tracked entities in its prompt, so it knows exactly what to exclude. Article batches are sized dynamically to fit within the model's context window (128k tokens for Llama 3.1, 25k for Groq models).

Only entities in competitive categories (competitor, exchange, wallet, protocol, infrastructure) with a relevance score of 6 or higher are stored. Each candidate includes the LLM's reasoning, visible in the dashboard so you can quickly judge quality.

Candidates are stored with a mention count. If a name keeps appearing across multiple news cycles (default: 3 separate cycles), the system fires a `COMPETITOR_NEW` alert. This threshold prevents one-off mentions from creating noise.

### What happens when you promote a candidate

When you see a candidate that looks like a real competitor, clicking **Promote** creates a new tracked entity automatically — no admin work needed. The system auto-populates the entity's news keywords, Reddit keywords, and even attempts a GitHub organisation lookup. The new entity enters the fetcher pipeline on the next scheduled cycle and starts accumulating data immediately.

If a candidate is not a real competitor, clicking **Dismiss** permanently excludes it from future scans.

**The question this system answers:** *"Are there companies or products appearing in the news alongside my tracked entities that I should be watching?"*

---

## 5. Momentum Tracking: Who's Moving Fastest?

Individual metrics tell you what changed. Alerts tell you when something is unusual. But neither answers the strategic question: **which competitor is gaining ground the fastest — and is that pace increasing or decreasing?**

The momentum system aggregates all of an entity's metric changes into a single score that measures *speed* (how fast it's moving right now) and *acceleration* (whether it's speeding up or slowing down).

### How it works

For each metric (stars, forks, ratings, mentions, etc.), the system computes the 7-day change for every entity. These raw velocities are incomparable — 500 new stars and a 0.1 rating increase are both meaningful but on completely different scales. So the system normalizes each metric's velocity across all entities using z-scores: "how many standard deviations above or below the average change is this entity?"

The entity's momentum score is the average of all its z-scores. An entity with a score of +1.2 is, on average, growing 1.2 standard deviations faster than the field across all its tracked metrics.

Acceleration is simply today's score minus yesterday's. Positive acceleration means the entity is gaining speed; negative means it's losing speed — even if the score itself is still high.

### The four quadrants

The dashboard plots every entity on a scatter chart with speed on the horizontal axis and acceleration on the vertical axis. This creates four quadrants:

- **Top-right (Dominant):** Fast and getting faster. These entities are pulling ahead.
- **Top-left (Rising Stars):** Not fast yet, but accelerating. If the trend holds, they will overtake the leaders. This is where you spot threats early.
- **Bottom-right (Fading Leaders):** Still ahead, but decelerating. Their lead is eroding.
- **Bottom-left (Stalling):** Slow and slowing further. Low priority unless they pivot.

**The question this system answers:** *"Who is gaining competitive ground the fastest, and is that pace increasing or decreasing?"*

---

## How It All Fits Together

The five systems form a pipeline where each layer builds on the previous one:

```
Data Collection (GitHub, News, App Store, Play Store, Twitter, Reddit, Changelog)
        |
        v
Layer 1: Smart Alerts
"Is this individual metric unusual for this entity?"
        |
        v
Layer 2: Compound Alerts
"Did multiple metrics move together in a meaningful pattern?"
        |
        v
Layer 3: Competitor Detection
"Are there new companies appearing in the news I should track?"
        |
        v
Layer 4: Momentum Tracking
"Who is gaining ground the fastest, and is it accelerating?"
        |
        v
Layer 5: Weekly Briefing
"What happened this week that matters?"
```

Every few hours, new data arrives from the various sources. The smart alert system evaluates each metric individually. The compound alert system then checks whether any multi-metric rules are triggered. The competitor detection system scans news headlines for untracked entities and filters them through an LLM. The momentum system aggregates all metric velocities into a single score per entity, tracking speed and acceleration. Once a week, the briefing system gathers everything — individual alerts, compound alerts, new competitor candidates, momentum shifts, and news — and produces a human-readable summary.

All five layers are visible in the dashboard. Single-metric and compound alerts appear in the alerts page. Competitor candidates have their own page linked from the alerts toolbar. Momentum has a dedicated leaderboard page with scatter plots and filterable time-series charts. Briefings have a dedicated page.

---

## The System Gets Smarter Over Time

The AI systems are not static. They improve as more data accumulates and as you interact with the dashboard:

- **Monthly:** The statistical models that define "normal" for each entity are retrained with the latest historical data. Entities that were too new to model may now have enough history for a personalized threshold.

- **Monthly:** The compound rule engine re-runs its evolutionary optimization, incorporating the latest alert dismissal history. New patterns may emerge as your team's triage behavior provides more training signal.

- **Every 2 hours:** The competitor detection system scans fresh news for untracked entities. Dismissing false positives permanently excludes them from future scans, so the candidates page gets cleaner over time.

- **Weekly:** Each briefing reflects the current state of alerts and metrics, automatically adapting to whatever the most important competitive signals are that week.

The system is designed so that simply using the dashboard — reading alerts, dismissing the ones you act on, promoting or dismissing competitor candidates, generating briefings — continuously improves the quality of future outputs. No manual tuning required.
