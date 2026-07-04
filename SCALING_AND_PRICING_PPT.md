# AI Voice & Chat Agent Platform — Scaling & Pricing Deck

---

## Slide 1: Fixed Costs + Raw Costs for All 3 Agents

### Fixed Infrastructure Cost (Monthly, shared across ALL customers)

| Item | Cost/month |
|------|-----------|
| AWS EC2 t3.large (2 vCPU, 8 GB RAM) | $60.00 |
| AWS EBS Storage (30 GB) | $2.40 |
| Twilio Phone Number (1 number) | $1.15 |
| **Total Fixed Cost** | **$63.55/month** |

This cost stays the same whether you have 1 customer or 10 customers.

### Raw Variable Costs Per Product (for 500 units)

| Product | Components | Raw Cost for 500 units |
|---------|-----------|----------------------|
| **Web Calling Agent** (500 min) | Deepgram STT ($3.85) + Google TTS ($3.00) + GPT-4.1-mini ($4.00) | **$10.85** |
| **Phone Calling Agent** (500 min) | Deepgram STT ($3.85) + Google TTS ($3.00) + GPT-4.1-mini ($4.00) + Twilio carrier ($7.00) + Number ($1.15) | **$19.00** |
| **WhatsApp Agent** (500 messages) | Twilio WhatsApp fees ($5.00) + GPT-4.1-mini ($1.00) | **$6.00** |

### Per-Minute / Per-Message Breakdown

| Product | STT | TTS | LLM | Telephony | Total/unit |
|---------|-----|-----|-----|-----------|-----------|
| Web Calling | $0.0077/min | $0.006/min | $0.008/min | $0 | **$0.022/min** |
| Phone Calling | $0.0077/min | $0.006/min | $0.008/min | $0.014/min | **$0.036/min** |
| WhatsApp | — | — | $0.002/msg | $0.010/msg | **$0.012/msg** |

---

## Slide 2: Pricing Plans — Web Calling Agent

| | Starter | **Pro (Recommended)** | Enterprise |
|---|---|---|---|
| **Price** | $29/month | **$50/month** | $199/month |
| Minutes included | 200 | **500** | 2000 |
| Concurrent calls | 2 | **5** | 10 |
| Overage rate | $0.15/min | $0.12/min | $0.09/min |
| Our raw cost | $4.34 | $10.85 | $43.40 |
| **Profit per customer** | $24.66 | **$39.15** | $155.60 |
| **Margin** | 85% | **78%** | 78% |

---

## Slide 3: Pricing Plans — Phone Calling Agent (Twilio)

| | Starter | **Pro (Recommended)** | Enterprise |
|---|---|---|---|
| **Price** | $39/month | **$60/month** | $249/month |
| Minutes included | 200 | **500** | 2000 |
| Concurrent calls | 2 | **5** | 10 |
| Dedicated phone number | ✅ | **✅** | ✅ (2 numbers) |
| Overage rate | $0.16/min | $0.14/min | $0.11/min |
| Our raw cost | $8.75 | $19.00 | $77.15 |
| **Profit per customer** | $30.25 | **$41.00** | $171.85 |
| **Margin** | 78% | **68%** | 69% |

---

## Slide 4: Pricing Plans — WhatsApp Agent

| | Starter | **Pro (Recommended)** | Enterprise |
|---|---|---|---|
| **Price** | $15/month | **$20/month** | $129/month |
| Messages included | 200 | **500** | 2000 |
| Response priority | Standard | **Priority** | Priority |
| Overage rate | $0.10/msg | $0.08/msg | $0.06/msg |
| Our raw cost | $2.40 | $6.00 | $24.00 |
| **Profit per customer** | $12.60 | **$14.00** | $105.00 |
| **Margin** | 84% | **70%** | 81% |

---

## Slide 5: Revenue Projection — Web Calling Agent

Fixed cost share: $63.55 ÷ total customers across all products (shared infra).
For simplicity, we allocate $21.18 of fixed cost to each product (÷3).

| Customers | Revenue | Raw Variable Cost | Fixed Share ($21.18) | **Total Cost** | **Monthly Profit** | **Margin** |
|-----------|---------|-------------------|---------------------|---------------|-------------------|-----------|
| 1 | $50 | $10.85 | $21.18 | $32.03 | **+$17.97** | 36% |
| 3 | $150 | $32.55 | $21.18 | $53.73 | **+$96.27** | 64% |
| 5 | $250 | $54.25 | $21.18 | $75.43 | **+$174.57** | 70% |
| 10 | $500 | $108.50 | $21.18 | $129.68 | **+$370.32** | 74% |

**Break-even: 1 customer** (revenue $50 > cost $32.03)

---

## Slide 6: Revenue Projection — Phone Calling Agent

| Customers | Revenue | Raw Variable Cost | Fixed Share ($21.18) | **Total Cost** | **Monthly Profit** | **Margin** |
|-----------|---------|-------------------|---------------------|---------------|-------------------|-----------|
| 1 | $60 | $19.00 | $21.18 | $40.18 | **+$19.82** | 33% |
| 3 | $180 | $57.00 | $21.18 | $78.18 | **+$101.82** | 57% |
| 5 | $300 | $95.00 | $21.18 | $116.18 | **+$183.82** | 61% |
| 10 | $600 | $190.00 | $21.18 | $211.18 | **+$388.82** | 65% |

**Break-even: 1 customer** (revenue $60 > cost $40.18)

---

## Slide 7: Revenue Projection — WhatsApp Agent

| Customers | Revenue | Raw Variable Cost | Fixed Share ($21.18) | **Total Cost** | **Monthly Profit** | **Margin** |
|-----------|---------|-------------------|---------------------|---------------|-------------------|-----------|
| 1 | $20 | $6.00 | $21.18 | $27.18 | **-$7.18** ❌ | Loss |
| 3 | $60 | $18.00 | $21.18 | $39.18 | **+$20.82** | 35% |
| 5 | $100 | $30.00 | $21.18 | $51.18 | **+$48.82** | 49% |
| 10 | $200 | $60.00 | $21.18 | $81.18 | **+$118.82** | 59% |

**Break-even: 2 customers** (2 × $20 = $40 > cost $33.18)

---

## Slide 8: Concurrency & Capacity

### Current Setup: EC2 t3.large (2 vCPU, 8 GB RAM)

**Maximum total concurrent voice calls: 8–10 (shared across ALL customers, ALL agents)**

WhatsApp messages do NOT consume concurrent slots — they're instant request-response, not persistent connections.

### How to allocate concurrency across customers:

| Total Voice Customers | Safe concurrency promise per customer | Total worst-case |
|----------------------|--------------------------------------|-----------------|
| 2 customers | 5 each | 10 ✅ |
| 3 customers | 3 each | 9 ✅ |
| 5 customers | 2 each | 10 ✅ |
| 5 customers | 3 each | 15 ⚠️ (overcommit) |

### What limits each component:

| Component | Limit | Concern? |
|-----------|-------|----------|
| Deepgram STT (streaming) | 150 concurrent | ❌ No |
| Google Cloud TTS | 1000+ RPM | ❌ No |
| OpenAI GPT-4.1-mini | 500 RPM, 200K TPM | ❌ No |
| Twilio (phone carrier) | No hard limit | ❌ No |
| **EC2 t3.large (RAM/CPU)** | **8–10 concurrent calls** | **✅ Only bottleneck** |

### What happens at overload?
- Existing calls continue fine
- New incoming calls fail to connect or experience lag
- Server doesn't crash — quality degrades for everyone

---

## Slide 9: Scaling Roadmap

```
PHASE 1 (Now)                PHASE 2 (Growth)              PHASE 3 (Scale)
────────────────────         ────────────────────          ────────────────────
EC2 t3.large                 EC2 c5.2xlarge                ECS Auto-scaling
2 vCPU, 8 GB RAM             8 vCPU, 16 GB RAM            Multiple containers
$64/month                    $248/month                   $400–600/month

Max concurrent: 8–10         Max concurrent: 25–30        Max concurrent: 50+
Target: 3–5 customers        Target: 10–15 customers      Target: 20+ customers

Upgrade trigger:             Upgrade trigger:             Upgrade trigger:
Regularly hitting            Regularly hitting            Unpredictable peaks,
8 concurrent calls           25 concurrent calls         need zero-downtime
```

### How to move between phases:

| Transition | Effort | Downtime |
|-----------|--------|----------|
| Phase 1 → Phase 2 | Stop instance → Change type → Start | **2 minutes** |
| Phase 2 → Phase 3 | Containerize + setup ECS + RDS + Redis | **1–2 weeks project** |

### Instance Options for Phase 2:

| Instance | vCPUs | RAM | Concurrent calls | Monthly |
|----------|-------|-----|-----------------|---------|
| t3.xlarge | 4 | 16 GB | ~15–20 | $121 |
| c5.xlarge | 4 | 8 GB | ~15–18 | $124 |
| c5.2xlarge | 8 | 16 GB | ~25–30 | $248 |

---

## Slide 10: Scaling Beyond — Enterprise Path

### When we reach 20+ customers (Phase 3):

**1. Negotiate Enterprise API rates:**
- Deepgram Enterprise: 30–50% discount on STT pricing
- OpenAI: Volume discounts at higher tiers
- Google Cloud: Committed use discounts
- Result: variable cost drops from $0.022/min → $0.012/min

**2. Reserved Instances on AWS:**
- 1-year commitment: 40% savings on compute
- c5.2xlarge drops from $248 → ~$155/month

**3. Self-hosted models (50+ customers):**
- Run Whisper (STT) on GPU → eliminates Deepgram cost
- Run open-source TTS → eliminates Google TTS cost
- Run Llama on GPU → eliminates OpenAI cost
- Fixed GPU cost (~$2,200/month) but handles unlimited requests
- Break-even vs API costs at ~15,000 min/month

**4. Margin improvement over time:**

| Phase | Our cost/min | Selling at $0.10/min | Margin |
|-------|-------------|---------------------|--------|
| Phase 1 (now) | $0.022/min | $0.10 | 78% |
| Phase 2 (enterprise APIs) | $0.014/min | $0.10 | 86% |
| Phase 3 (self-hosted) | $0.005/min | $0.10 | 95% |

---

## Slide 11: Revenue Targets & Break-Even

### Break-even by product:

| Product | Pro Price | Raw cost | Fixed share | Break-even at |
|---------|-----------|----------|-------------|---------------|
| Web Calling | $50 | $10.85 | $21.18 | **1 customer** |
| Phone Calling | $60 | $19.00 | $21.18 | **1 customer** |
| WhatsApp | $20 | $6.00 | $21.18 | **2 customers** |

### Annual Projections (Pro plans, per product):

**Web Calling Agent:**
| Customers | Monthly Profit | Annual Profit |
|-----------|---------------|---------------|
| 3 | $96 | $1,155 |
| 5 | $175 | $2,095 |
| 10 | $370 | $4,444 |

**Phone Calling Agent:**
| Customers | Monthly Profit | Annual Profit |
|-----------|---------------|---------------|
| 3 | $102 | $1,222 |
| 5 | $184 | $2,206 |
| 10 | $389 | $4,666 |

**WhatsApp Agent:**
| Customers | Monthly Profit | Annual Profit |
|-----------|---------------|---------------|
| 3 | $21 | $250 |
| 5 | $49 | $586 |
| 10 | $119 | $1,426 |

### Combined (if growing all 3 products equally):

| Customers per product | Total Monthly Profit | Total Annual Profit |
|----------------------|---------------------|---------------------|
| 3 each (9 total) | $219 | $2,627 |
| 5 each (15 total) | $408 | $4,887 |
| 10 each (30 total) | $878 | $10,536 |

---

## Slide 12: Next Steps

### Immediate (Week 1–2):
1. Deploy Dograh on EC2 t3.large with new stack (Deepgram + Google TTS + GPT-4.1-mini)
2. Configure Twilio for phone calling + WhatsApp integration
3. Migrate existing agents from Cartesia+Groq → new stack
4. Test all 3 products end-to-end

### Short-term (Month 1–2):
5. Launch Meta Ads campaign for customer acquisition
6. Onboard first 3–5 paying customers
7. Monitor server metrics (RAM, CPU, concurrent connections)
8. Validate unit economics with real usage data

### Medium-term (Month 3–6):
9. Hit 10 customers → upgrade to c5.xlarge or c5.2xlarge
10. Add usage dashboard for customers
11. Implement overage billing system
12. Begin enterprise API negotiations with Deepgram/OpenAI

### Long-term (Month 6–12):
13. Move to ECS auto-scaling if customer count exceeds 20
14. Explore self-hosted models for margin improvement
15. Expand language support (Arabic via Google TTS)

---

## Slide 13: When to Shift to Auto-Scaling

### You DON'T need auto-scaling when:
- Fewer than 15–20 customers
- Peak concurrent calls stay below 25
- Traffic is predictable (business hours only)
- You can tolerate 2 minutes downtime for manual scaling

### You NEED auto-scaling when:
- Total promised concurrency across customers exceeds 30
- Customers operate 24/7 across time zones
- Unpredictable traffic spikes (campaigns, viral events)
- SLA commitments require zero downtime
- Revenue justifies the complexity ($400–600/month infra)

### Decision framework:

```
Are you regularly hitting 8+ concurrent calls?
├── NO → Stay on single EC2, monitor monthly
└── YES → Are peaks > 25 concurrent?
    ├── NO → Upgrade to c5.2xlarge ($248/month), done
    └── YES → Are peaks unpredictable?
        ├── NO → Use c5.4xlarge ($496/month) for ~50 concurrent
        └── YES → Move to ECS auto-scaling ($400–600/month)
```

### Auto-scaling setup (ECS Fargate):

| Component | What changes | Monthly cost |
|-----------|-------------|-------------|
| API containers | Auto-scale 2–6 tasks based on connections | ~$200–400 |
| PostgreSQL | Move to RDS (managed) | ~$50–70 |
| Redis | Move to ElastiCache | ~$25 |
| Load Balancer | ALB routes to healthy tasks | ~$25 |
| S3 (recordings) | Replace MinIO | ~$5 |
| **Total** | | **~$350–525/month** |

### Key insight:
Auto-scaling is NOT about handling more traffic — it's about handling **unpredictable** traffic. If your load is predictable, a bigger single server is simpler, cheaper, and has fewer failure points.

---

## Summary: The Numbers That Matter

| Metric | Value |
|--------|-------|
| Fixed monthly cost | $63.55 |
| Raw cost per voice minute (web) | $0.022 |
| Raw cost per voice minute (phone) | $0.036 |
| Raw cost per WhatsApp message | $0.012 |
| Selling price | $0.10/min (web), $0.12/min (phone), $0.04/msg (WhatsApp) |
| Break-even | 1–2 customers |
| Max concurrent calls (Phase 1) | 8–10 |
| Server upgrade effort | 2 minutes, zero data loss |
