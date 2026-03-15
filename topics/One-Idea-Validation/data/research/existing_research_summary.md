# Existing Research Summary — Financial Dashboard for Creators

This document summarizes ALL prior research on the "Financial Dashboard for Creators" idea from two previous research projects: `micro-saas-_creators` and `micro-saas-deep-viability`. Do NOT re-research what's here — build on it, challenge it, and go deeper.

---

## Prior Verdict: BUILD (from micro-saas-deep-viability)

The idea was one of 3 survivors out of 11 ideas analyzed. The verdict was BUILD, but it was part of a broad scan — not a deep, focused validation. This project stress-tests that verdict.

---

## 1. What the Idea Is

"Mint.com for creators." A web app that connects to all income sources (YouTube AdSense, Patreon, Twitch, Stripe, etc.) via API and aggregates all financial data into a single real-time dashboard. Tracks business expenses and helps with tax estimations. AI categorizes expenses and provides proactive insights.

**Source:** micro-saas-_creators | Problem 2/20 | Score: All criteria PASS | Industry: Creator Economy

---

## 2. Problem Statement (from prior research)

Creators use spreadsheets to track fragmented income across multiple platforms. No existing affordable tool automatically syncs income from creator platforms. Generic accounting tools (QuickBooks, Wave, Xero) are not designed for creator workflows. Creators are buying spreadsheet templates on Etsy — a validation signal.

---

## 3. Revenue Model (proposed)

| Tier | Price | Includes |
|------|-------|----------|
| Side Hustle | $19/mo | Up to 3 income sources |
| Full-Time Creator | $49/mo | Unlimited sources + tax estimation |
| Creator Business | $99/mo | Advanced analytics + team access |

Path to $10K MRR: ~205 customers on $49/mo plan.

---

## 4. Customer Personas (from micro-saas-deep-viability)

### Persona 1: Chloe, Full-Time YouTuber
- 250K subscribers, earns $8-15K/mo from 5+ sources (AdSense, Patreon, sponsorships, affiliate, merch)
- Uses messy Google Sheet to track income, dreads tax season
- Would pay $49/mo for clarity and peace of mind
- Finds tools through creator podcasts (Colin & Samir), Twitter/X, creator communities
- Quote: "You've probably got money trickling in from sponsors, affiliate links, Patreon, merch drops... It adds up fast, and gets messy even faster if you're not tracking it."

### Persona 2: Marco, Growing Twitch Streamer (Part-Time)
- Works full-time job, streams 3-4 nights/week, earns a few hundred/mo
- Has no idea how much to set aside for taxes
- Highly price-sensitive — would not pay $50/mo, but $10-15/mo possible
- Finds tools through r/Twitch, Discord, peer recommendations
- Quote: "I'm starting to make a few hundred bucks a month from subs and bits... But I have no idea how much of that I need to set aside for taxes."

---

## 5. Competitors Identified (from prior research)

### Generic Accounting (not creator-specific)
| Competitor | Weakness |
|-----------|----------|
| QuickBooks Self-Employed | Not for creators. No platform integrations. Complex. |
| Wave | Generic. No YouTube/Patreon syncing. |
| Xero | Generic. Manual data entry or CSV uploads required. |
| Zeni.ai | High-cost service for startups, not individual creators. |

### Creator-Adjacent Companies (mentioned but not deeply researched)
- **Stir** — Raised $4M+, focused on money movement and revenue splitting between collaborators. NOT an analytics dashboard. Trajectory unclear — needs deep investigation.
- **Karat Financial** — Creator-focused banking/credit card. Different product category but in the creator finance space.
- **Cocountant** — Educational blog reviewing accounting software for creators. Not a competitor, but validates the problem exists.

### Open-Source: Virtually Non-Existent
- `iobi83/Creator-Finance-Tracker` — 0 stars, basic. No viable open-source competition.

---

## 6. Market Gaps Identified

1. **No direct integrations** with creator platforms (YouTube, Patreon, Twitch, Stripe)
2. **No purpose-built UX** for creator workflows (sponsorships, ad revenue, affiliate income)
3. **No actionable creator-specific insights** — generic tools just do bookkeeping

---

## 7. Moat Analysis (from micro-saas-deep-viability)

- **API Integration Network (High)**: Building and maintaining integrations is a significant barrier. First-mover who builds YouTube + Patreon + Twitch + Stripe connections has a head start.
- **Workflow Lock-in & Historical Data (High)**: Once a creator has months/years of categorized financial history, switching cost is immense.
- **AI/ML for Forecasting (Medium)**: Income forecasting, expense categorization, tax estimation add secondary moat.
- **Brand & Trust (Low initially)**: Becoming the trusted "QuickBooks for Creators" is powerful but takes time.

---

## 8. Unit Economics (from prior research)

- **Expected Monthly Churn**: 4-6% (it's a "painkiller" not a "vitamin")
- **ARPU**: $49/mo
- **Average Lifetime**: 20 months (at 5% churn)
- **LTV**: $980
- **CAC**: $200-300 (content marketing, creator affiliates, paid social)
- **LTV:CAC Ratio**: ~4.1:1 (healthy)

---

## 9. MVP Blueprint (from micro-saas-deep-viability)

### Core Features (v1)
- Secure onboarding & account connections (Plaid for banks, OAuth for platforms)
- Platform integrations: YouTube (Google APIs), Patreon, Stripe
- Unified revenue dashboard: total gross revenue, breakdown by source, 12-month trend chart
- Automated expense tracking: Plaid transactions, AI category suggestions, review/approve UI
- Profitability snapshot: Gross Revenue - Expenses = Net Profit

### Excluded from v1
- Tax calculation/advice (legal complexity)
- Invoicing & payments
- Brand deal management / CRM
- Multi-user / accountant access
- Mobile app (responsive web is enough)

### Proposed Tech Stack
- Frontend: React (Next.js) + Recharts
- Backend: Python (FastAPI)
- Database: PostgreSQL
- Auth: Clerk.dev or Auth0
- Key integrations: Plaid, Google API Client (YouTube), Patreon API, Stripe API

### Infrastructure Cost Estimates
| Scale | Monthly Cost |
|-------|-------------|
| 0 customers (dev) | ~$50 |
| 50 customers | ~$190 (Plaid ~$75 @ $1.50/user) |
| 200 customers | ~$600 (Plaid ~$300) |

---

## 10. Sales Channels (from micro-saas-deep-viability)

### First 10 Customers
- Reddit engagement (r/youtubers, r/Twitch, r/Patreon, r/CreatorServices)
- Discord/Slack creator communities
- Twitter/X direct engagement with creators discussing finance pain
- Frame as "feedback request," not sales pitch
- CAC: $0 (sweat equity)

### Scale to $10K MRR
- **Creator Affiliate Marketing** (primary): partner with creators who make content about creator business. 40% recurring commission. CAC ~$187/year.
- **Content Marketing**: SEO + YouTube targeting creator finance questions. Blended CAC $100-150 over time.

---

## 11. Competitive Threat: Stir

Prior research flagged Stir as the most significant competitive threat. Stir raised $4M+, has brand recognition and existing integrations. However, their focus has historically been on money movement and splitting revenue, not deep analytics. **This needs much deeper investigation in this project.** What is Stir's current status? Did they pivot? Are they still operating? Did creators actually adopt their product?

---

## 12. Key Open Questions (your research agenda)

1. Is the pain severe enough to justify $49/mo, or is a spreadsheet "good enough"?
2. What happened to Stir? What about Karat? Any other creator finance startups?
3. Can YouTube API actually expose earnings/revenue data? What about Twitch, TikTok?
4. Are creators actually willing to pay for non-content-creation tools at $30+/mo?
5. What does the minimum "I would pay for this" product look like?
6. Which creator segments are most reachable and most willing to pay?
7. Have YouTube/TikTok/Twitch improved their native financial dashboards enough to close the gap?

---

**NOTE**: The prior research was conducted in early-mid 2026. Check for any developments since then — new competitors, API changes, market shifts.
