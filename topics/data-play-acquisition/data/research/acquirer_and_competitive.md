

---

## 2. Secondary Acquirer: 1Password / Dashlane / LastPass

### Why Credential Managers Are Natural Acquirers

**Thesis**: Password managers are evolving into "access managers." AI agents represent a new class of credentials and permissions that need managing.

**1Password**: 
- Already expanded beyond passwords into secrets management (1Password for Developers), SSH keys, API tokens
- Acquired SecretHub (2021) for machine identity management
- Their product roadmap is moving from "human credentials" to "machine credentials" — AI agents are the ultimate machine credential
- A dataset showing which AI agents have which OAuth grants is essentially a credential inventory

**Dashlane**:
- $300M+ funding, consumer-focused
- Added Dark Web monitoring (identity surveillance)
- Natural extension: "AI Agent Permission Dashboard" alongside their password vault

**Strategic Value of Our Data**: The cross-platform permission map (which agents have OAuth access to which services) is essentially the credential graph for AI agents. This is exactly what 1Password/Dashlane would need to extend their vault to cover AI agent access.

---

## 3. Tertiary Acquirers: Enterprise Security Moving Consumer

### CrowdStrike
- $75B+ market cap, dominant in enterprise endpoint security
- Acquired Humio ($400M, 2021) for log management/observability
- Acquired Bionic ($350M, 2023) for application security posture management
- **Gap**: Zero consumer presence. Our product + dataset could be their consumer beachhead.
- **Risk**: CrowdStrike has no consumer DNA. Less likely acquirer unless they see consumer AI agent security as a wedge into SMB.

### Palo Alto Networks
- $120B+ market cap
- Acquired Talon Cyber Security ($625M, 2023) — enterprise browser security
- Acquired Dig Security ($400M, 2023) — data security posture management
- **Gap**: Talon acquisition shows interest in browser-based security — our browser extension model fits
- **Risk**: Same enterprise DNA problem. Consumer is not their market.

### CyberArk
- Published Jan 2026 report "What's Shaping the AI Agent Security Market in 2026"
- Focuses on machine identity for AI agents
- Already thinking about this space but from enterprise angle




---

## 5. Competitive Landscape (Cycle 3)

### Direct Competitors: Consumer AI Agent Security

**No direct consumer competitor exists as of Feb 2026.** No startup has shipped a consumer-facing tool that monitors AI agent security posture across platforms. The gap is real.

### Closest Adjacent Players

**Backslash Security — MCP Server Security Hub**
- Scans 7,000+ MCP servers for vulnerabilities
- Publicly searchable database with risk scores
- **BUT**: Developer/enterprise focused. No consumer product. No cross-agent telemetry.
- Our differentiation: We score the *consumer's configuration*, not the server itself. "Your Cursor + Jira MCP setup has a toxic flow vulnerability" vs. "This MCP server has a CVE."

**Snyk — MCP-Scan**
- Built a scanner specifically for MCP toxic flows (untrusted content + private data + public sink)
- Developer tool, open-source
- No consumer play. No behavioral telemetry.

**Backslash + Snyk together** represent the enterprise side of exactly what we'd build for consumers. They validate the attack surface but don't serve our market.

### Enterprise AI Agent Monitoring Tools (Could Move Downmarket)

**Obsidian Security**: Real-time AI agent monitoring for enterprises. SaaS security posture management. Could theoretically add consumer, but enterprise DNA makes this unlikely near-term.

**Protect AI**: ML model security platform. Enterprise only. Acquired by Trend Micro? Focus on model security, not agent permissions.

**Lakera**: AI security guardrails (prompt injection defense). API-level. No consumer product.

**Invariant Labs**: Agent security analysis. Research-oriented.

**Galileo AI / Braintrust / Langfuse / Arize Phoenix**: AI observability platforms for monitoring agents in production. All enterprise/developer focused. None consumer-facing.

### Key Competitive Insight
The entire AI agent security market is developer/enterprise focused. **The consumer gap is complete.** This is both opportunity and risk — the opportunity is clear blue ocean; the risk is that consumer demand might not yet exist.



---

## 7. Microsoft Security Dashboard for AI (Feb 13, 2026)

### What It Is
Microsoft launched **"Security Dashboard for AI"** in public preview, providing CISOs and risk leaders with a unified, real-time view of AI risks across agents, apps, and platforms.

**Key capabilities**:
- Aggregates posture and real-time risk signals from Defender, Entra, and Purview
- **AI inventory** covering AI agents, models, MCP servers, and applications
- Covers Microsoft 365 Copilot, Copilot Studio agents, Foundry apps — **AND third-party: Google Gemini, OpenAI ChatGPT, and MCP servers**
- AI risk scorecard for organizations
- Shadow AI agent discovery using Security Copilot
- No additional licensing required for existing Microsoft Security customers

**Critical stat from announcement**: "53% of security professionals say their current AI risk management needs improvement"

### Implications for Our Thesis

**Enterprise, not consumer**: This is squarely enterprise-focused (CISOs, risk leaders, governance committees). It validates the AI agent security market but doesn't address consumer/prosumer.

**Sets the frame**: Microsoft defining "AI risk scorecard" and "AI inventory" as product categories normalizes these concepts. When consumers eventually want the same visibility, they'll already understand the concept.

**Third-party coverage is notable**: Microsoft covering Google Gemini and OpenAI ChatGPT in their dashboard shows cross-platform monitoring is technically feasible and market-expected.

**Not a direct competitor**: Different market segment (enterprise vs. consumer), different distribution (bundled with Microsoft 365 vs. standalone product).

### Updated Competitive Map

| Player | Segment | Focus | Threat to Us |
|--------|---------|-------|-------------|
| Gen Digital Agent Trust Hub | Consumer | Trust verification/certification of AI skills | HIGH — same market, but different approach |
| Microsoft Security Dashboard | Enterprise | Unified AI risk visibility | LOW — different segment, validates market |
| Backslash Security | Developer | MCP server vulnerability scanning | LOW — different segment |
| Snyk MCP-Scan | Developer | Toxic flow detection | LOW — different segment |
| WitnessAI | Enterprise | Agentic AI governance | LOW — different segment |




---

## CYCLE 24 UPDATE: New Competitor — Overmind (London, Feb 2026)

### Overmind — "Intelligence-Grade" AI Agent Security

- **Founded**: 2025, London
- **Raised**: €2.3M Seed (Feb 2026), led by Osney Capital + Antler, 14Peaks, Portfolio Ventures, Endurance Ventures
- **Team**: CEO Tyler Edwards (8 years MI5/MI6/GCHQ building AI systems), CTO from Monzo/Lyst, CRO scaled 3 unicorns
- **Target**: Enterprise — legal, healthcare, FinTech (NOT consumer)
- **Product**: Deployment-layer infrastructure monitoring agent interactions, detecting behavioral drift in real-time, with reinforcement learning for improvement
- **Key quote (CEO)**: "The AI security industry is trying to secure the wrong thing. Models will always be vulnerable to adversarial inputs... But what happens when an agent is live in production, interacting with real systems, and its behaviour starts to drift?"

### Competitive Assessment vs. Our Thesis

**Overmind is enterprise-only.** Their GTM targets regulated industries (legal, healthcare, FinTech) with managed agent deployments. They do NOT serve consumers or prosumers.

**Key differences**:
| Dimension | Overmind | Our Product |
|-----------|----------|-------------|
| Target | Enterprise DevOps/SecOps | Individual consumers/prosumers |
| Form factor | DevTool / platform | Browser extension + dashboard |
| Agents monitored | Custom enterprise agents | ChatGPT, Claude, Gemini, open-source |
| Pricing | Enterprise SaaS | Freemium ($0-$9.99/mo) |
| Data play | Enterprise behavioral patterns | Consumer cross-platform telemetry |
| Moat | MI5 pedigree, RL capabilities | Consumer trust, aggregated dataset |

**Implications**: Overmind validates the CATEGORY (AI agent behavioral monitoring) but leaves the consumer market completely open. In fact, Overmind could be a future ACQUIRER — they might want consumer data to benchmark enterprise agent behavior.

### Updated European AI Agent Security Funding Context

Per the Overmind announcement, the European AI agent security ecosystem is active:
- **Archestra** (London): €2.8M pre-Seed — safely connecting AI agents to internal data
- **Equixly** (Italy): €10M — AI-driven API security testing
- **Qevlar AI** (France): €9.1M — agentic AI for SOCs
- Combined with Overmind: **€26M+** into AI agent security/governance in EU alone

### New Security Companies to Track

From the MCP vulnerability research:
- **Cyata** — AI security startup that found the Anthropic Git MCP CVEs. Core team approach.
- **BlueRock Security** — Runtime security platform that found the Microsoft MarkItDown SSRF. Analyzed 7,000+ MCP servers.
- **Koi** — Supply chain gateway for MCP servers. Found the postmark-mcp malicious attack. Risk engine detects behavioral anomalies.

These three are all enterprise-focused but demonstrate the growing vendor ecosystem around MCP security specifically.
