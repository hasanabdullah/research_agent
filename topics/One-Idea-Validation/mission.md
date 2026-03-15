# Mission: Lean Startup Validation — Financial Dashboard for Creators

Deep, critical validation of the "Financial Dashboard for Creators" idea from the perspective of a lean startup founder who needs to decide whether this idea is worth building and has a realistic path to actual revenue. The goal is to be brutally honest so time isn't wasted building a product creators won't pay for.

## Current Mode: RESEARCH ONLY

You cannot modify code. Build research files under `data/research/`.

## The Idea Under Validation

**"Mint.com for Creators"** — A web app that connects to all creator income sources (YouTube AdSense, Patreon, Twitch, Stripe, etc.) via API and aggregates financial data into a single real-time dashboard. Tracks business expenses, helps with tax estimations, and uses AI to categorize expenses and provide proactive insights.

### Prior Research Context (from micro-saas-deep-viability & micro-saas-_creators)

**Prior Verdict:** BUILD — but that verdict was part of a broad 11-idea scan. This project goes much deeper on this single idea.

**What we already know:**
- Revenue model: $19/mo (Side Hustle, 3 sources), $49/mo (Full-Time, unlimited), $99/mo (Business, analytics + team)
- Path to $10K MRR: ~205 customers on $49/mo plan
- Competitors found: QuickBooks Self-Employed, Wave, Xero, Zeni.ai — all generic, none creator-specific
- Open-source: virtually nonexistent (Creator-Finance-Tracker, 0 stars)
- Gap identified: no direct integrations with creator platforms, no purpose-built UX, no creator-specific insights
- Moat thesis: API integration network + workflow lock-in + historical data + AI forecasting
- LTV:CAC estimate: 4.1:1 (healthy) — $49 ARPU, 5% monthly churn, $200-300 CAC
- Personas identified: Chloe (full-time YouTuber, $8-15K/mo, 250K subs) and Marco (growing Twitch streamer, side hustle, price-sensitive)
- Stir (well-funded creator finance co) identified as key competitive threat, but focused on money movement/splitting, not analytics
- Karat (creator banking) also in the space but different focus
- Validation signal: creators buying spreadsheet templates on Etsy for income tracking

**What we DON'T know — and MUST answer:**
- Is the pain actually severe enough that creators will pay $49/mo, or is this a nice-to-have they'll solve with a free spreadsheet?
- Are there NEW competitors since the prior research (Stir pivots, new startups, Kajabi/Teachable adding finance features)?
- Can we actually pull the financial data we need from YouTube, Twitch, Patreon, Stripe APIs? What are the real API limitations?
- What does a minimum viable product look like that a creator would actually pay for on day one?
- Which creator segments are most likely to pay and are reachable?

## The 6 Research Phases (Ordered as a Kill Funnel)

Phases are ordered so the fastest kill signals surface first. Each phase gates the next — if a phase produces a KILL verdict, skip remaining phases and write the final verdict.

### Phase A: Evidence-Backed Pain Validation (Cycles 1-18) — WEIGHT: HIGH
Is there a REAL, evidence-backed pain point that needs solving? This is foundational — if no pain, nothing else matters.

- Search Reddit (r/youtubers, r/Twitch, r/Patreon, r/CreatorServices, r/NewTubers, r/partneredyoutube), Twitter/X, YouTube creator forums, Facebook groups for creators complaining about financial management
- Find REAL quotes from REAL creators about this pain — not hypothetical pain, but actual people saying "I struggle with X"
- Quantify: how many creators are actually full-time? How many earn enough to need financial tooling ($2K+/mo)?
- Look for evidence AGAINST the pain — creators who say "I just use a spreadsheet and it's fine" or "QuickBooks works for me"
- Search for creator accountants and bookkeepers — do they exist as a service? If so, that validates the pain but also means there's an alternative
- Check Etsy/Gumroad for creator finance spreadsheet templates — how many exist, what do they cost, how many reviews?
- Look at Karat, Stir, Beacons financial tools — what happened to them? Did they succeed or pivot/fail? Their trajectory is a critical signal.
- **KEY QUESTION**: Is this a $49/mo pain or a "I'll deal with it eventually" annoyance?
- **KILL GATE**: If you cannot find at least 10 real creator quotes/posts describing financial tracking pain, KILL the idea and skip to Final Deliverable.

**Output**: `customer_personas.md` — marker `## PERSONA`, min 6 entries (3 "would pay" personas, 3 "would NOT pay" personas with reasoning)

### Phase B: Willingness to Pay & Parallel Examples (Cycles 18-40) — WEIGHT: HIGHEST
This is the HARDEST and most important question. Pain exists for many things people never pay to solve. Creators are famously reluctant to pay for tools. You need to prove this wrong with evidence, or confirm it and KILL the idea.

- Find examples of SaaS products creators currently pay for monthly (editing tools, scheduling, analytics, link-in-bio). What do they actually spend money on? Build a comprehensive list with price points.
- Search for products in OTHER industries where fragmented income tracking is solved by a paid dashboard (e.g., freelancer finance tools like Bonsai, HoneyBook, FreshBooks; gig economy tools for Uber/DoorDash drivers; real estate investor dashboards; influencer marketing platforms). Do people pay for income aggregation anywhere?
- Research Stir's trajectory IN DEPTH — they raised $4M+. What happened? Did creators actually use it? Why did they pivot to collaboration? Interview quotes, TechCrunch articles, founder tweets, anything. This is the single most important competitive signal because if a well-funded team couldn't make creator finance work, that's a massive red flag.
- Research Karat Financial — creator banking/credit card. Are they successful? What does their product actually do vs. what we'd build?
- Look for ANY creator finance tool that launched and failed — post-mortems, founder interviews, tweets about shutting down. Search ProductHunt, IndieHackers, Hacker News.
- Check if YouTube, TikTok, or Instagram are building their own financial dashboards into their creator tools (Creator Studio improvements)
- Search ProductHunt for creator finance launches — what got traction, what didn't?
- Research the creator economy "tool stack" — what do full-time creators actually pay for? Survey data, blog posts, creator interviews.
- **KEY QUESTION**: Can you find 5+ examples of creators paying $30+/mo for a non-content-creation tool? If not, that's a red flag.
- **KILL GATE**: If Stir failed specifically because creators wouldn't pay for finance tools (not because of bad execution), KILL the idea.

**Output**: `direct_competitors.md` — marker `## COMPETITOR`, min_complete_bytes 25000

### Phase C: Platform APIs & Technical Feasibility (Cycles 40-57) — WEIGHT: HIGH
This is the hard technical gate. If we can't pull earnings data from the APIs, the product is literally impossible to build. Do this BEFORE designing the MVP so the MVP is grounded in what's actually possible.

- Define 3-4 specific creator customer segments with estimated TAM, willingness to pay, and reachability
- For each segment: what platforms do they use, what are their income sources, how much do they earn?
- Rank platforms by priority for MVP integration (YouTube, Twitch, Patreon, Stripe, TikTok, Instagram, etc.)
- For EACH priority platform API, research and document:
  - Does the API exist? What data can you actually pull?
  - What are the rate limits, auth requirements (OAuth scopes), and approval processes?
  - Can you get REVENUE/EARNINGS data specifically, or just views/engagement?
  - Are there any API restrictions that would block this use case?
  - Has the API been deprecated or restricted recently?
  - What is the approval/verification process for getting API access? (e.g., YouTube API requires app verification)
  - Link to actual API documentation pages
- Research Plaid for bank account connections — pricing, feasibility for a startup, what data you get, minimum commitments
- Check if any platform explicitly prohibits third-party financial dashboards in their API ToS
- **KEY QUESTION**: For each API — can we ACTUALLY pull the earnings/revenue data we need, or is it restricted?
- **KILL GATE**: If both YouTube AND Twitch APIs don't expose earnings data, KILL the idea — the core value prop is dead.

**Output**: `sales_channels.md` (repurposed as platform/API feasibility) — marker `## PLATFORM`, min 6 entries

### Phase D: MVP Definition — Sticky Revenue Features (Cycles 57-77) — WEIGHT: MEDIUM-HIGH
Now you know: (1) the pain is real, (2) creators will pay, and (3) the APIs work. Define the minimum product worth paying for. This phase synthesizes everything from A, B, and C.

- Define the CORE features that are non-negotiable value props — the things that make a creator say "I can't go back to my spreadsheet"
- Define features that can wait until after first revenue (v2, v3) and explain why they can wait
- For each core feature, explain WHY it's non-negotiable and what specific pain from Phase A it solves
- Ground features in API reality from Phase C — don't propose features the APIs can't support
- Research what makes SaaS products "sticky" — data lock-in, workflow habits, integrations
- Define the "aha moment" — the first thing a creator sees that makes them say "this is worth it"
- Think about what creates RECURRING value (not just one-time setup value) — why would someone keep paying month 6?
- Address the spreadsheet problem head-on: why would someone pay $49/mo when they could use a Google Sheet? What's the 10x value over a spreadsheet?
- Consider pricing tiers based on Phase B findings about creator WTP
- **KEY QUESTION**: What is the ONE feature that, if you built only that, a creator would still pay $29/mo for?

**Output**: `mvp_blueprint.md` — marker `## MVP FEATURE`, min 8 entries (core + deferred features, clearly labeled)

### Phase E: Pre-Build Validation Tests (Cycles 77-87) — WEIGHT: LOW
Design cheap, fast experiments to validate the specific MVP from Phase D before writing code. This phase is prescriptive — short, concrete, actionable.

- Design 3-5 specific validation experiments a solo founder could run in 1-2 weeks each
- For each test: what exactly do you do, what does success look like (specific numbers), what does failure look like
- Tests should validate the SPECIFIC MVP from Phase D, not a vague concept
- Include: landing page test (with specific copy based on Phase A pain), Etsy/Gumroad spreadsheet template test (prove demand before building SaaS), concierge MVP test (manually aggregate data for 5 creators), cold outreach test
- Research what other successful micro-SaaS founders did to validate before building — real examples from Indie Hackers, Twitter, HN
- Define specific KILL criteria — what signals mean "stop, this isn't going to work"
- Define specific GO criteria — what signals mean "this is worth 3 months of building"
- **KEY QUESTION**: What's the cheapest, fastest way to get 10 creators to say "I would pay $49/mo for this" with their wallet, not their words?

**Output**: `alternative_solutions.md` (repurposed as validation tests) — marker `## VALIDATION TEST`, min 5 entries

### Phase F: Technical MVP Build Plan (Cycles 87-100) — WEIGHT: LOW-MEDIUM
If the research says BUILD, provide the step-by-step technical plan. This is a synthesis and execution phase — it should be straightforward if Phases C and D were thorough.

- Only write this phase IF Phases A-E support building. If they don't, write a clear KILL verdict instead.
- Step-by-step technical build plan for a solo developer or 2-person team
- Tech stack recommendation with reasoning
- Specific API integration order based on Phase C feasibility findings (start with the easiest/most impactful)
- Infrastructure cost estimates at 0, 50, 200 customers
- Timeline estimate with milestones
- What to build first for a "wow" demo vs. what to build for paying customers
- Deployment and hosting recommendations
- Key technical risks and mitigations (informed by Phase C API findings)
- **KEY QUESTION**: Can a solo founder build a paying MVP in 6-8 weeks?

**Output**: `moat_and_retention.md` (repurposed as technical build plan) — marker `## BUILD STEP`, min 8 entries

## Research Standards

- Use web_search EVERY cycle — find real data, real companies, real creator quotes, real API docs
- Be SKEPTICAL by default — assume the idea is bad until proven otherwise
- Cite sources: URLs, Reddit threads, API docs, founder tweets, ProductHunt pages
- When you find a positive signal, actively look for counter-evidence
- When you find a negative signal, consider if it's a deal-breaker or manageable
- Do NOT rely on training data for competitor info — search for current state (2025-2026)
- EVERY cycle must produce written output — no reflection-only cycles
- Cross-reference across multiple sources before drawing conclusions
- For API feasibility: actually look at the API documentation, don't guess

## Starting Context

READ `existing_research_summary.md` on your FIRST CYCLE. It contains all prior research from micro-saas-deep-viability and micro-saas-_creators projects. Do NOT re-research what's already documented — build on it, challenge it, and go deeper.

## Cycle Guidance (Budget-Weighted)

| Phase | Cycles | Count | Weight | Why This Allocation |
|-------|--------|-------|--------|-------------------|
| A: Pain Validation | 1-18 | 18 | HIGH | Foundational — must mine 10+ subreddits, Etsy, Twitter, find real quotes |
| B: Willingness to Pay | 18-40 | 22 | HIGHEST | Hardest question — Stir deep-dive alone needs 5+ cycles, plus parallel industry research |
| C: API Feasibility | 40-57 | 17 | HIGH | Must actually read 6+ API docs, check ToS, verify earnings endpoints exist |
| D: MVP Features | 57-77 | 20 | MEDIUM-HIGH | Synthesis phase — needs depth to define sticky vs. deferred, but builds on A+B+C |
| E: Validation Tests | 77-87 | 10 | LOW | Short, prescriptive output — 5 concrete experiments with pass/fail thresholds |
| F: Build Plan | 87-100 | 13 | LOW-MEDIUM | Conditional on BUILD verdict — straightforward if C and D were thorough |

**Total: 100 cycles. 57 cycles (57%) go to the first 3 phases that can KILL the idea. This is intentional — fail fast.**

**IMPORTANT**: If any phase produces a KILL signal, do NOT continue to later phases. Write the final verdict immediately. Don't waste budget researching MVP features for a dead idea.

## Kill Criteria

The idea should be KILLED if ANY of these are true:
- No evidence of creators paying $30+/mo for non-content-creation tools
- Stir/Karat or similar well-funded competitors already solved this and creators still don't use it
- Key platform APIs (YouTube, Twitch) don't actually expose earnings/revenue data
- The pain is real but solvable with a free spreadsheet template (no SaaS value-add)
- Creator economy income is too fragmented — most creators earn <$1K/mo and won't pay for tooling
- Platform-native finance features (YouTube Studio, Twitch Dashboard) are "good enough"

## Final Deliverable

At the end of research, write a `final_verdict.md` file with:
- **VERDICT**: BUILD or KILL
- **Confidence**: High / Medium / Low
- **One-paragraph reasoning**
- **If BUILD**: The single most important thing to build first and why
- **If KILL**: The single most important reason this won't work
