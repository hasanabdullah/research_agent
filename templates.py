"""Pre-defined research templates for Deepshika topic creation.

Each template provides curated research phases with specific questions,
producing consistent, high-quality research structures instantly with zero LLM cost.

Structure: Template (the goal) -> Research Phases (the questions) -> Output files (the answers)
"""

TEMPLATES = [
    {
        "slug": "startup-idea-generator",
        "name": "Startup Idea Generator",
        "description": "Validate and flesh out a startup idea from problem to GTM strategy",
        "icon": "🚀",
        "mission_template": (
            "# Mission: {topic}\n\n"
            "{description}\n\n"
            "## Current Mode: RESEARCH ONLY\n\n"
            "You cannot modify code. Build research files under `data/research/`.\n\n"
            "## Objective\n\n"
            "Systematically validate a startup idea by researching the problem space, "
            "existing solutions, target customers, business model viability, and go-to-market strategy.\n\n"
            "## Output Files\n\n{phase_list}\n"
        ),
        "agent_parameters_template": (
            "You are Deepshika, a research agent investigating: {topic}.\n"
            "You are in RESEARCH ONLY mode — only research files under data/research/.\n"
            "You have web_search and web_fetch tools — USE THEM EVERY CYCLE.\n"
            "Read 1-2 of your existing files to understand current state, then SEARCH the web, then WRITE.\n"
            "EVERY CYCLE MUST PRODUCE WRITTEN OUTPUT — either append_to_file or propose_edit.\n"
            "Do NOT just reflect. Reflecting without writing is wasting a cycle.\n"
            "Use `append_to_file` to ADD sections to existing research files.\n"
            "Use `propose_edit` only when creating a new file.\n"
            "IMPORTANT: Keep each tool call's content under 2000 characters.\n"
            "Be brutally honest. Name real companies, cite real data.\n\n"
            "## Research Phases\n\n{phase_list}\n"
        ),
        "phases": [
            {
                "filename": "problem_analysis.md",
                "title": "Problem Analysis",
                "question": "What is the core problem and who experiences it most acutely?",
                "guiding_questions": [
                    "How do people currently solve this problem?",
                    "What are the biggest pain points with current solutions?",
                    "How frequently does this problem occur?",
                    "What is the cost of the problem (time, money, frustration)?",
                ],
            },
            {
                "filename": "market_sizing.md",
                "title": "Market Sizing",
                "question": "How large is the addressable market (TAM/SAM/SOM)?",
                "guiding_questions": [
                    "What is the total addressable market?",
                    "What segments can you realistically capture first?",
                    "What is the market growth rate?",
                    "Are there adjacent markets to expand into?",
                ],
            },
            {
                "filename": "competitive_landscape.md",
                "title": "Competitive Landscape",
                "question": "Who are the existing players and what are their weaknesses?",
                "guiding_questions": [
                    "Who are the direct competitors?",
                    "Who are indirect competitors or substitutes?",
                    "What are competitor pricing models?",
                    "Where are the gaps in current offerings?",
                ],
            },
            {
                "filename": "solution_design.md",
                "title": "Solution Design",
                "question": "What would a differentiated solution look like?",
                "guiding_questions": [
                    "What is the core value proposition?",
                    "What is the minimum viable product?",
                    "What technology stack is needed?",
                    "What are the key technical risks?",
                ],
            },
            {
                "filename": "business_model.md",
                "title": "Business Model",
                "question": "How will this business make money and scale?",
                "guiding_questions": [
                    "What pricing model works best?",
                    "What are the unit economics?",
                    "What is the expected customer acquisition cost?",
                    "What are the key cost drivers?",
                ],
            },
            {
                "filename": "gtm_strategy.md",
                "title": "Go-to-Market Strategy",
                "question": "How do you acquire the first 100 customers?",
                "guiding_questions": [
                    "What channels will you use for distribution?",
                    "Who are the early adopters?",
                    "What is the launch strategy?",
                    "What partnerships could accelerate growth?",
                ],
            },
        ],
    },
    {
        "slug": "investment-thesis-builder",
        "name": "Investment Thesis Builder",
        "description": "Build a comprehensive investment thesis for a sector, company, or asset class",
        "icon": "💰",
        "mission_template": (
            "# Mission: {topic}\n\n"
            "{description}\n\n"
            "## Current Mode: RESEARCH ONLY\n\n"
            "You cannot modify code. Build research files under `data/research/`.\n\n"
            "## Objective\n\n"
            "Build a rigorous investment thesis by analyzing macro trends, market dynamics, "
            "key players, financial metrics, risks, and potential returns.\n\n"
            "## Output Files\n\n{phase_list}\n"
        ),
        "agent_parameters_template": (
            "You are Deepshika, a research agent investigating: {topic}.\n"
            "You are in RESEARCH ONLY mode — only research files under data/research/.\n"
            "You have web_search and web_fetch tools — USE THEM EVERY CYCLE.\n"
            "Read 1-2 of your existing files to understand current state, then SEARCH the web, then WRITE.\n"
            "EVERY CYCLE MUST PRODUCE WRITTEN OUTPUT — either append_to_file or propose_edit.\n"
            "Do NOT just reflect. Reflecting without writing is wasting a cycle.\n"
            "Use `append_to_file` to ADD sections to existing research files.\n"
            "Use `propose_edit` only when creating a new file.\n"
            "IMPORTANT: Keep each tool call's content under 2000 characters.\n"
            "Be brutally honest. Name real companies, cite real financials.\n\n"
            "## Research Phases\n\n{phase_list}\n"
        ),
        "phases": [
            {
                "filename": "macro_landscape.md",
                "title": "Macro Landscape",
                "question": "What macro trends support or threaten this investment?",
                "guiding_questions": [
                    "What economic, regulatory, or demographic tailwinds exist?",
                    "What are the key headwinds or risks?",
                    "How does the current market cycle affect timing?",
                    "What geopolitical factors are relevant?",
                ],
            },
            {
                "filename": "sector_analysis.md",
                "title": "Sector Analysis",
                "question": "What is the sector structure and competitive dynamics?",
                "guiding_questions": [
                    "What is the sector growth rate and maturity?",
                    "Who are the dominant players and emerging disruptors?",
                    "What are typical margins and capital requirements?",
                    "What are the barriers to entry?",
                ],
            },
            {
                "filename": "company_deep_dive.md",
                "title": "Company Deep Dive",
                "question": "What are the fundamentals, moat, and management quality?",
                "guiding_questions": [
                    "What is the revenue model and growth trajectory?",
                    "What competitive advantages are durable?",
                    "How strong is the management team?",
                    "What is the balance sheet health?",
                ],
            },
            {
                "filename": "financial_modeling.md",
                "title": "Financial Modeling",
                "question": "What are the key financial metrics and valuation?",
                "guiding_questions": [
                    "What are current and projected revenues/earnings?",
                    "What valuation multiples are appropriate?",
                    "How does the valuation compare to peers?",
                    "What are the bull/base/bear case scenarios?",
                ],
            },
            {
                "filename": "risk_assessment.md",
                "title": "Risk Assessment",
                "question": "What could go wrong and how likely is it?",
                "guiding_questions": [
                    "What are the key operational risks?",
                    "What regulatory or legal risks exist?",
                    "What is the downside scenario?",
                    "How correlated is this to the broader market?",
                ],
            },
            {
                "filename": "thesis_summary.md",
                "title": "Thesis Summary",
                "question": "What is the final investment recommendation and why?",
                "guiding_questions": [
                    "What is the expected return profile?",
                    "What is the recommended position sizing?",
                    "What are the key catalysts and timeline?",
                    "What would invalidate the thesis?",
                ],
            },
        ],
    },
    {
        "slug": "market-validator",
        "name": "Market Validator",
        "description": "Validate whether a market opportunity is real, growing, and accessible",
        "icon": "✅",
        "mission_template": (
            "# Mission: {topic}\n\n"
            "{description}\n\n"
            "## Current Mode: RESEARCH ONLY\n\n"
            "You cannot modify code. Build research files under `data/research/`.\n\n"
            "## Objective\n\n"
            "Validate a market opportunity by examining customer demand signals, market size, "
            "growth trajectories, willingness to pay, and competitive intensity.\n\n"
            "## Output Files\n\n{phase_list}\n"
        ),
        "agent_parameters_template": (
            "You are Deepshika, a research agent investigating: {topic}.\n"
            "You are in RESEARCH ONLY mode — only research files under data/research/.\n"
            "You have web_search and web_fetch tools — USE THEM EVERY CYCLE.\n"
            "Read 1-2 of your existing files to understand current state, then SEARCH the web, then WRITE.\n"
            "EVERY CYCLE MUST PRODUCE WRITTEN OUTPUT — either append_to_file or propose_edit.\n"
            "Do NOT just reflect. Reflecting without writing is wasting a cycle.\n"
            "Use `append_to_file` to ADD sections to existing research files.\n"
            "Use `propose_edit` only when creating a new file.\n"
            "IMPORTANT: Keep each tool call's content under 2000 characters.\n"
            "Be brutally honest. Name real companies, cite real data.\n\n"
            "## Research Phases\n\n{phase_list}\n"
        ),
        "phases": [
            {
                "filename": "demand_signals.md",
                "title": "Demand Signals",
                "question": "Is there real, measurable demand for this solution?",
                "guiding_questions": [
                    "What search volume and trends exist for related keywords?",
                    "Are people actively asking for solutions in forums/communities?",
                    "What waitlists or pre-orders exist for similar products?",
                    "What adjacent product traction can you reference?",
                ],
            },
            {
                "filename": "customer_segments.md",
                "title": "Customer Segments",
                "question": "Who are the most promising customer segments?",
                "guiding_questions": [
                    "What are the distinct customer personas?",
                    "Which segment has the highest urgency?",
                    "What is each segment's budget and buying process?",
                    "How reachable are these segments?",
                ],
            },
            {
                "filename": "market_size_dynamics.md",
                "title": "Market Size & Dynamics",
                "question": "How big is the market and how fast is it growing?",
                "guiding_questions": [
                    "What is the TAM/SAM/SOM?",
                    "What is the compound annual growth rate?",
                    "What forces are driving growth or contraction?",
                    "Is this a new market or an existing one being disrupted?",
                ],
            },
            {
                "filename": "willingness_to_pay.md",
                "title": "Willingness to Pay",
                "question": "What will customers pay and how do you know?",
                "guiding_questions": [
                    "What do existing alternatives cost?",
                    "What is the switching cost for customers?",
                    "What pricing experiments have been done?",
                    "What is the value-to-price ratio?",
                ],
            },
            {
                "filename": "competitive_intensity.md",
                "title": "Competitive Intensity",
                "question": "How crowded is the market and is there room for a new entrant?",
                "guiding_questions": [
                    "How many funded startups are in this space?",
                    "Are incumbents well-positioned to dominate?",
                    "What is the level of customer lock-in?",
                    "Where are the underserved niches?",
                ],
            },
            {
                "filename": "validation_verdict.md",
                "title": "Validation Verdict",
                "question": "Go/no-go — does the evidence support pursuing this market?",
                "guiding_questions": [
                    "What is the strength of evidence for demand?",
                    "Is the market large enough and growing?",
                    "Can you compete effectively?",
                    "What are the critical unknowns remaining?",
                ],
            },
        ],
    },
    {
        "slug": "competitive-intelligence",
        "name": "Competitive Intelligence",
        "description": "Deep-dive analysis of competitors — strategy, products, positioning, and weaknesses",
        "icon": "🔍",
        "mission_template": (
            "# Mission: {topic}\n\n"
            "{description}\n\n"
            "## Current Mode: RESEARCH ONLY\n\n"
            "You cannot modify code. Build research files under `data/research/`.\n\n"
            "## Objective\n\n"
            "Map and analyze the competitive landscape including competitor strategies, "
            "product capabilities, pricing, positioning, and exploitable weaknesses.\n\n"
            "## Output Files\n\n{phase_list}\n"
        ),
        "agent_parameters_template": (
            "You are Deepshika, a research agent investigating: {topic}.\n"
            "You are in RESEARCH ONLY mode — only research files under data/research/.\n"
            "You have web_search and web_fetch tools — USE THEM EVERY CYCLE.\n"
            "Read 1-2 of your existing files to understand current state, then SEARCH the web, then WRITE.\n"
            "EVERY CYCLE MUST PRODUCE WRITTEN OUTPUT — either append_to_file or propose_edit.\n"
            "Do NOT just reflect. Reflecting without writing is wasting a cycle.\n"
            "Use `append_to_file` to ADD sections to existing research files.\n"
            "Use `propose_edit` only when creating a new file.\n"
            "IMPORTANT: Keep each tool call's content under 2000 characters.\n"
            "Be brutally honest. Name real companies, cite real data.\n\n"
            "## Research Phases\n\n{phase_list}\n"
        ),
        "phases": [
            {
                "filename": "competitor_map.md",
                "title": "Competitor Map",
                "question": "Who are all the competitors (direct, indirect, potential)?",
                "guiding_questions": [
                    "Who are the direct competitors with similar products?",
                    "Who are indirect competitors solving the same problem differently?",
                    "Which large companies could enter this space?",
                    "What startups are emerging in this area?",
                ],
            },
            {
                "filename": "product_comparison.md",
                "title": "Product Comparison",
                "question": "How do competitor products compare feature-by-feature?",
                "guiding_questions": [
                    "What are the core features of each competitor?",
                    "Where does each product excel or fall short?",
                    "What is the user experience like?",
                    "What is the technology stack and architecture?",
                ],
            },
            {
                "filename": "pricing_analysis.md",
                "title": "Pricing Analysis",
                "question": "How do competitors price and package their products?",
                "guiding_questions": [
                    "What are the pricing tiers and models?",
                    "How does pricing compare across competitors?",
                    "What discounting strategies are used?",
                    "What is the perceived value-to-price ratio?",
                ],
            },
            {
                "filename": "positioning_messaging.md",
                "title": "Positioning & Messaging",
                "question": "How do competitors position themselves and to whom?",
                "guiding_questions": [
                    "What is each competitor's core value proposition?",
                    "What customer segments do they target?",
                    "What messaging and branding do they use?",
                    "How do they differentiate from each other?",
                ],
            },
            {
                "filename": "strengths_weaknesses.md",
                "title": "Strengths & Weaknesses",
                "question": "What are the exploitable weaknesses and dangerous strengths?",
                "guiding_questions": [
                    "What do customer reviews and complaints reveal?",
                    "Where are competitors investing (hiring, R&D)?",
                    "What are their funding and financial positions?",
                    "What organizational or strategic weaknesses exist?",
                ],
            },
            {
                "filename": "strategic_opportunities.md",
                "title": "Strategic Opportunities",
                "question": "Where can you win against the competition?",
                "guiding_questions": [
                    "What gaps exist in the competitive landscape?",
                    "Which customer segments are underserved?",
                    "What differentiation strategies are viable?",
                    "What timing advantages exist?",
                ],
            },
        ],
    },
    {
        "slug": "technology-radar",
        "name": "Technology Radar",
        "description": "Evaluate emerging technologies — maturity, adoption, risks, and strategic fit",
        "icon": "📡",
        "mission_template": (
            "# Mission: {topic}\n\n"
            "{description}\n\n"
            "## Current Mode: RESEARCH ONLY\n\n"
            "You cannot modify code. Build research files under `data/research/`.\n\n"
            "## Objective\n\n"
            "Evaluate emerging technologies by assessing maturity, adoption curves, "
            "key players, implementation challenges, and strategic implications.\n\n"
            "## Output Files\n\n{phase_list}\n"
        ),
        "agent_parameters_template": (
            "You are Deepshika, a research agent investigating: {topic}.\n"
            "You are in RESEARCH ONLY mode — only research files under data/research/.\n"
            "You have web_search and web_fetch tools — USE THEM EVERY CYCLE.\n"
            "Read 1-2 of your existing files to understand current state, then SEARCH the web, then WRITE.\n"
            "EVERY CYCLE MUST PRODUCE WRITTEN OUTPUT — either append_to_file or propose_edit.\n"
            "Do NOT just reflect. Reflecting without writing is wasting a cycle.\n"
            "Use `append_to_file` to ADD sections to existing research files.\n"
            "Use `propose_edit` only when creating a new file.\n"
            "IMPORTANT: Keep each tool call's content under 2000 characters.\n"
            "Be brutally honest. Name real projects, cite real benchmarks.\n\n"
            "## Research Phases\n\n{phase_list}\n"
        ),
        "phases": [
            {
                "filename": "technology_overview.md",
                "title": "Technology Overview",
                "question": "What is this technology and how does it work?",
                "guiding_questions": [
                    "What is the core technical innovation?",
                    "How does it differ from existing approaches?",
                    "What are the foundational papers or projects?",
                    "What are the key technical concepts to understand?",
                ],
            },
            {
                "filename": "maturity_assessment.md",
                "title": "Maturity Assessment",
                "question": "Where is this technology on the maturity curve?",
                "guiding_questions": [
                    "Is this in research, early adoption, or mainstream?",
                    "What production deployments exist?",
                    "What are the known limitations and failure modes?",
                    "What benchmarks or performance data exist?",
                ],
            },
            {
                "filename": "ecosystem_players.md",
                "title": "Ecosystem & Players",
                "question": "Who is building, funding, and adopting this technology?",
                "guiding_questions": [
                    "What companies are leading development?",
                    "What is the open-source vs proprietary landscape?",
                    "How much funding is flowing into this space?",
                    "Who are the early adopters and what are their results?",
                ],
            },
            {
                "filename": "implementation_challenges.md",
                "title": "Implementation Challenges",
                "question": "What are the practical barriers to adoption?",
                "guiding_questions": [
                    "What skills and infrastructure are required?",
                    "What are the integration challenges?",
                    "What are the cost implications?",
                    "What are common failure patterns in implementation?",
                ],
            },
            {
                "filename": "use_cases.md",
                "title": "Use Cases",
                "question": "What are the most promising applications?",
                "guiding_questions": [
                    "What problems does this technology solve uniquely well?",
                    "What industries or domains benefit most?",
                    "What are the highest-ROI use cases?",
                    "What use cases are overhyped vs underexplored?",
                ],
            },
            {
                "filename": "strategic_recommendation.md",
                "title": "Strategic Recommendation",
                "question": "Should you adopt, watch, or ignore this technology?",
                "guiding_questions": [
                    "What is the risk of early adoption vs waiting?",
                    "What is the timeline for mainstream readiness?",
                    "How does this fit your strategic priorities?",
                    "What experiments or pilots would reduce uncertainty?",
                ],
            },
        ],
    },
    {
        "slug": "customer-discovery",
        "name": "Customer Discovery",
        "description": "Research customer needs, behaviors, and jobs-to-be-done for product development",
        "icon": "👥",
        "mission_template": (
            "# Mission: {topic}\n\n"
            "{description}\n\n"
            "## Current Mode: RESEARCH ONLY\n\n"
            "You cannot modify code. Build research files under `data/research/`.\n\n"
            "## Objective\n\n"
            "Deeply understand target customers — their needs, behaviors, decision processes, "
            "and unmet jobs-to-be-done — to inform product and go-to-market decisions.\n\n"
            "## Output Files\n\n{phase_list}\n"
        ),
        "agent_parameters_template": (
            "You are Deepshika, a research agent investigating: {topic}.\n"
            "You are in RESEARCH ONLY mode — only research files under data/research/.\n"
            "You have web_search and web_fetch tools — USE THEM EVERY CYCLE.\n"
            "Read 1-2 of your existing files to understand current state, then SEARCH the web, then WRITE.\n"
            "EVERY CYCLE MUST PRODUCE WRITTEN OUTPUT — either append_to_file or propose_edit.\n"
            "Do NOT just reflect. Reflecting without writing is wasting a cycle.\n"
            "Use `append_to_file` to ADD sections to existing research files.\n"
            "Use `propose_edit` only when creating a new file.\n"
            "IMPORTANT: Keep each tool call's content under 2000 characters.\n"
            "Be brutally honest. Name real companies, cite real user research.\n\n"
            "## Research Phases\n\n{phase_list}\n"
        ),
        "phases": [
            {
                "filename": "customer_personas.md",
                "title": "Customer Personas",
                "question": "Who are the distinct customer archetypes?",
                "guiding_questions": [
                    "What are the key demographic and firmographic traits?",
                    "What roles and titles do target users have?",
                    "What is a day-in-the-life for each persona?",
                    "What motivations and frustrations define each persona?",
                ],
            },
            {
                "filename": "jobs_to_be_done.md",
                "title": "Jobs to Be Done",
                "question": "What jobs are customers hiring a solution to do?",
                "guiding_questions": [
                    "What functional jobs need to be done?",
                    "What emotional and social jobs exist?",
                    "What are the desired outcomes for each job?",
                    "Which jobs are most underserved?",
                ],
            },
            {
                "filename": "current_workflows.md",
                "title": "Current Workflows",
                "question": "How do customers currently solve this problem?",
                "guiding_questions": [
                    "What tools and processes are currently used?",
                    "Where are the friction points and workarounds?",
                    "How much time and money is spent on current solutions?",
                    "What triggers a search for a new solution?",
                ],
            },
            {
                "filename": "buying_process.md",
                "title": "Buying Process",
                "question": "How do customers evaluate and purchase solutions?",
                "guiding_questions": [
                    "Who are the decision makers and influencers?",
                    "What is the typical evaluation process?",
                    "What criteria matter most in vendor selection?",
                    "What is the typical sales cycle length?",
                ],
            },
            {
                "filename": "pain_points.md",
                "title": "Pain Points & Unmet Needs",
                "question": "What are the deepest unmet needs and frustrations?",
                "guiding_questions": [
                    "What do customers complain about in reviews/forums?",
                    "What features are most requested but missing?",
                    "What would customers pay more for?",
                    "What problems do customers not even realize they have?",
                ],
            },
            {
                "filename": "customer_insights_summary.md",
                "title": "Customer Insights Summary",
                "question": "What are the key actionable insights for product development?",
                "guiding_questions": [
                    "What are the top 3 customer needs to address?",
                    "What is the ideal customer profile (ICP)?",
                    "What messaging will resonate most?",
                    "What features should be prioritized?",
                ],
            },
        ],
    },
    {
        "slug": "business-model-explorer",
        "name": "Business Model Explorer",
        "description": "Explore and compare business model options for a product or service",
        "icon": "📊",
        "mission_template": (
            "# Mission: {topic}\n\n"
            "{description}\n\n"
            "## Current Mode: RESEARCH ONLY\n\n"
            "You cannot modify code. Build research files under `data/research/`.\n\n"
            "## Objective\n\n"
            "Explore and compare business model options by analyzing revenue models, "
            "cost structures, distribution channels, and unit economics across similar companies.\n\n"
            "## Output Files\n\n{phase_list}\n"
        ),
        "agent_parameters_template": (
            "You are Deepshika, a research agent investigating: {topic}.\n"
            "You are in RESEARCH ONLY mode — only research files under data/research/.\n"
            "You have web_search and web_fetch tools — USE THEM EVERY CYCLE.\n"
            "Read 1-2 of your existing files to understand current state, then SEARCH the web, then WRITE.\n"
            "EVERY CYCLE MUST PRODUCE WRITTEN OUTPUT — either append_to_file or propose_edit.\n"
            "Do NOT just reflect. Reflecting without writing is wasting a cycle.\n"
            "Use `append_to_file` to ADD sections to existing research files.\n"
            "Use `propose_edit` only when creating a new file.\n"
            "IMPORTANT: Keep each tool call's content under 2000 characters.\n"
            "Be brutally honest. Name real companies, cite real financials.\n\n"
            "## Research Phases\n\n{phase_list}\n"
        ),
        "phases": [
            {
                "filename": "revenue_models.md",
                "title": "Revenue Models",
                "question": "What revenue models work in this space and why?",
                "guiding_questions": [
                    "What revenue models do competitors use?",
                    "What are the pros/cons of subscription vs transactional vs marketplace?",
                    "What pricing tiers and packaging strategies exist?",
                    "How do revenue models correlate with company success?",
                ],
            },
            {
                "filename": "cost_structure.md",
                "title": "Cost Structure",
                "question": "What does the cost structure look like at different scales?",
                "guiding_questions": [
                    "What are the fixed vs variable costs?",
                    "What are the major cost drivers?",
                    "How do costs scale with growth?",
                    "Where can costs be optimized?",
                ],
            },
            {
                "filename": "unit_economics.md",
                "title": "Unit Economics",
                "question": "What do the unit economics look like and when do you break even?",
                "guiding_questions": [
                    "What is the customer lifetime value (LTV)?",
                    "What is the customer acquisition cost (CAC)?",
                    "What is the LTV:CAC ratio?",
                    "What is the payback period?",
                ],
            },
            {
                "filename": "distribution_channels.md",
                "title": "Distribution Channels",
                "question": "What are the most effective channels to reach customers?",
                "guiding_questions": [
                    "What channels do successful competitors use?",
                    "What is the cost per acquisition for each channel?",
                    "Which channels are saturated vs emerging?",
                    "What organic/viral growth mechanisms are possible?",
                ],
            },
            {
                "filename": "comparable_models.md",
                "title": "Comparable Business Models",
                "question": "What can you learn from analogous businesses in other industries?",
                "guiding_questions": [
                    "What companies in adjacent spaces have similar dynamics?",
                    "What business model innovations have worked elsewhere?",
                    "What failed models should you avoid?",
                    "What hybrid models could work?",
                ],
            },
            {
                "filename": "model_recommendation.md",
                "title": "Model Recommendation",
                "question": "Which business model best fits your situation and why?",
                "guiding_questions": [
                    "Which model maximizes value capture?",
                    "Which model is most defensible?",
                    "What is the right starting model vs long-term model?",
                    "What experiments can validate the model?",
                ],
            },
        ],
    },
    {
        "slug": "regulatory-landscape",
        "name": "Regulatory Landscape",
        "description": "Map the regulatory environment, compliance requirements, and policy risks",
        "icon": "⚖️",
        "mission_template": (
            "# Mission: {topic}\n\n"
            "{description}\n\n"
            "## Current Mode: RESEARCH ONLY\n\n"
            "You cannot modify code. Build research files under `data/research/`.\n\n"
            "## Objective\n\n"
            "Map the regulatory environment including current regulations, pending legislation, "
            "compliance requirements, enforcement trends, and policy risks.\n\n"
            "## Output Files\n\n{phase_list}\n"
        ),
        "agent_parameters_template": (
            "You are Deepshika, a research agent investigating: {topic}.\n"
            "You are in RESEARCH ONLY mode — only research files under data/research/.\n"
            "You have web_search and web_fetch tools — USE THEM EVERY CYCLE.\n"
            "Read 1-2 of your existing files to understand current state, then SEARCH the web, then WRITE.\n"
            "EVERY CYCLE MUST PRODUCE WRITTEN OUTPUT — either append_to_file or propose_edit.\n"
            "Do NOT just reflect. Reflecting without writing is wasting a cycle.\n"
            "Use `append_to_file` to ADD sections to existing research files.\n"
            "Use `propose_edit` only when creating a new file.\n"
            "IMPORTANT: Keep each tool call's content under 2000 characters.\n"
            "Be brutally honest. Name real laws, cite real enforcement actions.\n\n"
            "## Research Phases\n\n{phase_list}\n"
        ),
        "phases": [
            {
                "filename": "current_regulations.md",
                "title": "Current Regulations",
                "question": "What laws and regulations currently govern this space?",
                "guiding_questions": [
                    "What are the key federal/national regulations?",
                    "What state/regional variations exist?",
                    "What international regulations apply?",
                    "What industry self-regulatory standards exist?",
                ],
            },
            {
                "filename": "compliance_requirements.md",
                "title": "Compliance Requirements",
                "question": "What specific compliance obligations must be met?",
                "guiding_questions": [
                    "What licenses or certifications are required?",
                    "What data protection and privacy rules apply?",
                    "What reporting and disclosure requirements exist?",
                    "What are the compliance costs and timelines?",
                ],
            },
            {
                "filename": "pending_legislation.md",
                "title": "Pending Legislation",
                "question": "What regulatory changes are in the pipeline?",
                "guiding_questions": [
                    "What bills or proposals are being considered?",
                    "What is the likely timeline for new regulations?",
                    "What lobby groups are active and what do they want?",
                    "How would proposed changes affect the business?",
                ],
            },
            {
                "filename": "enforcement_trends.md",
                "title": "Enforcement Trends",
                "question": "How are regulators enforcing existing rules?",
                "guiding_questions": [
                    "What recent enforcement actions have occurred?",
                    "What penalties have been imposed?",
                    "Which areas are seeing increased scrutiny?",
                    "How do different jurisdictions approach enforcement?",
                ],
            },
            {
                "filename": "risk_mitigation.md",
                "title": "Risk Mitigation",
                "question": "How can regulatory risks be managed?",
                "guiding_questions": [
                    "What compliance frameworks should be adopted?",
                    "What legal counsel or partnerships are needed?",
                    "How can products be designed for regulatory flexibility?",
                    "What insurance or risk transfer options exist?",
                ],
            },
            {
                "filename": "regulatory_strategy.md",
                "title": "Regulatory Strategy",
                "question": "What is the recommended regulatory strategy?",
                "guiding_questions": [
                    "Should you lead compliance or follow competitors?",
                    "What regulatory advantages can be built?",
                    "How should you engage with regulators?",
                    "What jurisdictions are most favorable to operate in?",
                ],
            },
        ],
    },
    {
        "slug": "trend-analysis",
        "name": "Trend Analysis",
        "description": "Identify and evaluate trends shaping an industry or market",
        "icon": "📈",
        "mission_template": (
            "# Mission: {topic}\n\n"
            "{description}\n\n"
            "## Current Mode: RESEARCH ONLY\n\n"
            "You cannot modify code. Build research files under `data/research/`.\n\n"
            "## Objective\n\n"
            "Identify and evaluate key trends shaping the industry — separating signal from noise, "
            "assessing impact timelines, and identifying strategic implications.\n\n"
            "## Output Files\n\n{phase_list}\n"
        ),
        "agent_parameters_template": (
            "You are Deepshika, a research agent investigating: {topic}.\n"
            "You are in RESEARCH ONLY mode — only research files under data/research/.\n"
            "You have web_search and web_fetch tools — USE THEM EVERY CYCLE.\n"
            "Read 1-2 of your existing files to understand current state, then SEARCH the web, then WRITE.\n"
            "EVERY CYCLE MUST PRODUCE WRITTEN OUTPUT — either append_to_file or propose_edit.\n"
            "Do NOT just reflect. Reflecting without writing is wasting a cycle.\n"
            "Use `append_to_file` to ADD sections to existing research files.\n"
            "Use `propose_edit` only when creating a new file.\n"
            "IMPORTANT: Keep each tool call's content under 2000 characters.\n"
            "Be brutally honest. Name real data sources, cite real statistics.\n\n"
            "## Research Phases\n\n{phase_list}\n"
        ),
        "phases": [
            {
                "filename": "trend_identification.md",
                "title": "Trend Identification",
                "question": "What are the major trends affecting this space?",
                "guiding_questions": [
                    "What technology trends are emerging?",
                    "What consumer behavior shifts are happening?",
                    "What economic or demographic forces are at play?",
                    "What cultural or social trends are relevant?",
                ],
            },
            {
                "filename": "trend_evidence.md",
                "title": "Trend Evidence",
                "question": "What data supports or contradicts each trend?",
                "guiding_questions": [
                    "What quantitative data exists for each trend?",
                    "What leading indicators are visible?",
                    "What anecdotal evidence supports the trend?",
                    "What counter-evidence exists?",
                ],
            },
            {
                "filename": "impact_timeline.md",
                "title": "Impact Timeline",
                "question": "When will each trend materially affect the market?",
                "guiding_questions": [
                    "Which trends are already impacting the market?",
                    "Which trends are 1-3 years away?",
                    "Which trends are 5+ years out?",
                    "What trigger events would accelerate each trend?",
                ],
            },
            {
                "filename": "winners_losers.md",
                "title": "Winners & Losers",
                "question": "Who benefits and who gets disrupted by each trend?",
                "guiding_questions": [
                    "What companies are best positioned for each trend?",
                    "What business models become obsolete?",
                    "What new categories or players will emerge?",
                    "What skills and capabilities become more valuable?",
                ],
            },
            {
                "filename": "second_order_effects.md",
                "title": "Second-Order Effects",
                "question": "What non-obvious consequences will these trends create?",
                "guiding_questions": [
                    "What new problems will these trends create?",
                    "What unexpected intersections between trends exist?",
                    "What supply chain or ecosystem effects will occur?",
                    "What regulatory responses are likely?",
                ],
            },
            {
                "filename": "strategic_implications.md",
                "title": "Strategic Implications",
                "question": "What should you do differently based on these trends?",
                "guiding_questions": [
                    "Which trends create the biggest opportunities?",
                    "Which trends pose the biggest threats?",
                    "What bets should you make now?",
                    "What hedging strategies are appropriate?",
                ],
            },
        ],
    },
    {
        "slug": "partnership-ecosystem-map",
        "name": "Partnership & Ecosystem Map",
        "description": "Map potential partners, integrations, and ecosystem dynamics",
        "icon": "🤝",
        "mission_template": (
            "# Mission: {topic}\n\n"
            "{description}\n\n"
            "## Current Mode: RESEARCH ONLY\n\n"
            "You cannot modify code. Build research files under `data/research/`.\n\n"
            "## Objective\n\n"
            "Map the ecosystem of potential partners, integrations, distribution allies, "
            "and co-development opportunities to accelerate growth.\n\n"
            "## Output Files\n\n{phase_list}\n"
        ),
        "agent_parameters_template": (
            "You are Deepshika, a research agent investigating: {topic}.\n"
            "You are in RESEARCH ONLY mode — only research files under data/research/.\n"
            "You have web_search and web_fetch tools — USE THEM EVERY CYCLE.\n"
            "Read 1-2 of your existing files to understand current state, then SEARCH the web, then WRITE.\n"
            "EVERY CYCLE MUST PRODUCE WRITTEN OUTPUT — either append_to_file or propose_edit.\n"
            "Do NOT just reflect. Reflecting without writing is wasting a cycle.\n"
            "Use `append_to_file` to ADD sections to existing research files.\n"
            "Use `propose_edit` only when creating a new file.\n"
            "IMPORTANT: Keep each tool call's content under 2000 characters.\n"
            "Be brutally honest. Name real companies, cite real partnerships.\n\n"
            "## Research Phases\n\n{phase_list}\n"
        ),
        "phases": [
            {
                "filename": "ecosystem_overview.md",
                "title": "Ecosystem Overview",
                "question": "What does the broader ecosystem look like?",
                "guiding_questions": [
                    "What are the key value chain participants?",
                    "What platforms and marketplaces exist?",
                    "What are the data and integration flows?",
                    "What ecosystem dynamics drive growth?",
                ],
            },
            {
                "filename": "partner_categories.md",
                "title": "Partner Categories",
                "question": "What types of partners would create the most value?",
                "guiding_questions": [
                    "What technology/integration partners are needed?",
                    "What distribution/channel partners exist?",
                    "What co-marketing partners would be valuable?",
                    "What strategic/investment partners make sense?",
                ],
            },
            {
                "filename": "target_partners.md",
                "title": "Target Partners",
                "question": "Who are the specific high-priority partner candidates?",
                "guiding_questions": [
                    "Which companies have complementary products?",
                    "Which companies serve the same customers?",
                    "What is each candidate's partnership track record?",
                    "What is the strategic rationale for each partnership?",
                ],
            },
            {
                "filename": "partnership_models.md",
                "title": "Partnership Models",
                "question": "What partnership structures and deal terms work best?",
                "guiding_questions": [
                    "What types of partnerships exist (referral, reseller, integration)?",
                    "What revenue sharing models are common?",
                    "What are typical partnership terms and agreements?",
                    "What exclusivity considerations exist?",
                ],
            },
            {
                "filename": "integration_landscape.md",
                "title": "Integration Landscape",
                "question": "What technical integrations would unlock the most value?",
                "guiding_questions": [
                    "What APIs and platforms should you integrate with?",
                    "What data sharing opportunities exist?",
                    "What are the technical requirements for key integrations?",
                    "What integration marketplaces should you be on?",
                ],
            },
            {
                "filename": "partnership_roadmap.md",
                "title": "Partnership Roadmap",
                "question": "What is the sequenced plan for building partnerships?",
                "guiding_questions": [
                    "Which partnerships should you pursue first?",
                    "What internal capabilities do you need to build?",
                    "What is the outreach and negotiation strategy?",
                    "What metrics should you track for partnership success?",
                ],
            },
        ],
    },
]

_TEMPLATE_INDEX = {t["slug"]: t for t in TEMPLATES}


def get_template(slug: str) -> dict | None:
    """Look up a template by slug. Returns None if not found."""
    return _TEMPLATE_INDEX.get(slug)


def list_templates() -> list[dict]:
    """Return summaries of all templates for UI picker, including phase details."""
    return [
        {
            "slug": t["slug"],
            "name": t["name"],
            "description": t["description"],
            "icon": t["icon"],
            "phase_count": len(t["phases"]),
            "phases": [
                {
                    "title": p["title"],
                    "filename": p["filename"],
                    "question": p["question"],
                    "guiding_questions": p["guiding_questions"],
                }
                for p in t["phases"]
            ],
        }
        for t in TEMPLATES
    ]


def apply_template(slug: str, topic_name: str, description: str) -> dict | None:
    """Apply a template and return the same dict shape as generate_agent_scaffold().

    Returns {"agent_parameters": str, "mission": str, "output_files": [...], "research_buckets": [...]}
    or None if the slug is not found.

    No "_usage" key = no LLM cost recorded.
    """
    template = get_template(slug)
    if template is None:
        return None

    # Build phase list for template interpolation
    phase_lines = []
    for i, phase in enumerate(template["phases"], 1):
        phase_lines.append(f"{i}. **{phase['title']}** — `{phase['filename']}`")
        phase_lines.append(f"   - {phase['question']}")
    phase_list = "\n".join(phase_lines)

    # Interpolate mission and agent_parameters
    mission = template["mission_template"].format(
        topic=topic_name,
        description=description or f"Research topic: {topic_name}",
        phase_list=phase_list,
    )
    agent_parameters = template["agent_parameters_template"].format(
        topic=topic_name,
        phase_list=phase_list,
    )

    # Build output_files (same shape as LLM scaffold)
    output_files = []
    for phase in template["phases"]:
        output_files.append({
            "filename": phase["filename"],
            "question": phase["question"],
        })

    # Build research_buckets (same shape as LLM scaffold)
    buckets = []
    for i, phase in enumerate(template["phases"], 1):
        buckets.append({
            "name": f"Phase {i}: {phase['title']}",
            "files": [phase["filename"]],
            "notion_page_id": "",
        })

    return {
        "agent_parameters": agent_parameters,
        "mission": mission,
        "output_files": output_files,
        "research_buckets": buckets,
    }
