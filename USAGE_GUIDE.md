# AI Seller Bot - Usage Guide

## Overview

AI Seller Bot is a universal Telegram sales assistant with multi-domain support. It can operate in two modes:

1. **textile_manufacturing** - Sells textile manufacturing services (швейная фабрика)
2. **ai_seller_self** - Sells AI bot implementation services

## Quick Start

### 1. Configure Environment

Copy `.env.example` to `.env` and fill in required values:

```bash
cp .env.example .env
```

Required variables:
- `TELEGRAM_TOKEN` - Your Telegram bot token
- `OPENAI_API_KEY` - OpenAI API key for LLM
- `AI_SELLER_DOMAIN` - Domain mode: `textile_manufacturing` or `ai_seller_self`
- `ADMIN_CHAT_IDS` - Comma-separated admin Telegram chat IDs

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Run the Bot

**Development (Polling Mode):**
```bash
python run_bot.py --mode polling
```

**Production (Webhook Mode):**
```bash
python run_bot.py --mode webhook --host 0.0.0.0 --port 8000
```

**Override Domain:**
```bash
# Run in AI seller self-promotion mode
python run_bot.py --mode polling --domain ai_seller_self

# Run in textile manufacturing mode
python run_bot.py --mode polling --domain textile_manufacturing
```

## Domain Configuration

### textile_manufacturing Mode

- **Purpose**: Sell textile manufacturing services
- **FSM**: Product inquiry → Work scheme → Quantity/Colors → Fabric → Contact → Order
- **Config**: Uses legacy flow_manager.py
- **Features**: MOQ validation, pricing rules, CRM integration

### ai_seller_self Mode

- **Purpose**: Sell AI bot implementation services
- **FSM**: Greeting → Qualify → Diagnose → Demo → Offer → Objections → Close
- **Config**: `config/domains/ai_seller_self/`
- **Features**: Conversation logging, learning from outcomes, lead tracking

## Admin Commands

Admin commands are available only to users listed in `ADMIN_CHAT_IDS`:

### Domain Management

```
/domain - Show current domain info
/switch_domain <domain_name> - Switch to another domain
/switch_domain - List available domains
```

### Conversation Tracking

```
/result <conv_id> success [reason]
/result <conv_id> fail <reason> [notes]
```

Example:
```
/result conv_12345 success
/result conv_67890 fail price_objection Client said too expensive
```

### Statistics

```
/stats [domain] - Conversation statistics
/learning_stats [context] - Learning system statistics
```

### Help

```
/admin_help - Show all admin commands
```

## Domain Switching

### Via Environment Variable

Set in `.env`:
```
AI_SELLER_DOMAIN=ai_seller_self
```

Then restart the bot.

### Via Admin Command

```
/switch_domain ai_seller_self
```

The bot will immediately switch to the new domain without restart.

## AI Seller Self Mode - Details

### 7-Stage Sales Funnel

1. **GREETING** - Welcome and initial engagement
2. **QUALIFY** - Collect business niche, lead volume
3. **DIAGNOSE** - Identify pain points and current solutions
4. **DEMO** - Show how AI bot solves their problems
5. **OFFER** - Present pricing (50k/150k/300k RUB)
6. **OBJECTIONS** - Handle concerns
7. **CLOSE** - Get commitment or schedule follow-up

### Data Collection

The bot automatically extracts and stores:
- `business_niche` - Client's business vertical
- `current_lead_volume` - Monthly lead count
- `lead_channels` - Where they get leads
- `pain_point` - Main challenges
- `current_solution` - What they use now
- `decision_authority` - Can they decide
- `budget_range` - Available budget
- `timeline` - When they want to start

### Conversation Logging

All conversations are logged to SQLite database:

```bash
# View database
sqlite3 data/conversations.db

# Check conversations
SELECT * FROM conversations ORDER BY started_at DESC LIMIT 10;

# Check messages
SELECT * FROM messages WHERE conversation_id = 'conv_12345';
```

### Learning System

The bot learns from conversation outcomes:

1. **Record Outcome** (admin):
   ```
   /result conv_12345 success
   /result conv_67890 fail no_budget
   ```

2. **View Learning Stats**:
   ```
   /learning_stats
   /learning_stats QUALIFY:ai_seller_self
   ```

3. **How It Works**:
   - Bot tries different response variants
   - Tracks which variants lead to success
   - Epsilon-greedy selection (80% best, 20% explore)
   - Automatically improves over time

### Behavior Rules

Configured in `config/domains/ai_seller_self/prompts/system_prompt.md`:

✅ **Allowed:**
- Be helpful and consultative
- Ask clarifying questions
- Show value proposition
- Handle objections professionally
- Use real success metrics

❌ **Forbidden:**
- Lie or make false claims
- Be pushy or aggressive
- Ignore "no" signals
- Rush the client
- Discount without permission

## File Structure

```
python-core/
├── run_bot.py                    # Main entry point
├── bot/
│   ├── webhook.py                # Webhook mode server
│   ├── message_router.py         # Polling mode handler
│   └── admin_commands.py         # Admin command handlers
├── dialog/
│   ├── flow_router.py            # Routes to correct flow manager
│   ├── flow_manager.py           # Legacy (textile_manufacturing)
│   └── universal_flow_manager.py # New (ai_seller_self)
├── config/
│   └── domains/
│       ├── textile_manufacturing/
│       └── ai_seller_self/
│           ├── domain.yaml       # Domain metadata
│           ├── slots.yaml        # Data to collect
│           ├── states.yaml       # FSM definition
│           └── prompts/
│               └── system_prompt.md  # Bot personality
└── utils/
    ├── domain_loader.py          # Domain config loader
    ├── conversation_logger.py    # SQLite logging
    └── simple_learner.py         # Learning system
```

## Troubleshooting

### Bot doesn't respond

1. Check Telegram token: `echo $TELEGRAM_TOKEN`
2. Check domain config: `python -c "from utils.domain_loader import get_current_domain_name; print(get_current_domain_name())"`
3. Check logs for errors

### Admin commands not working

1. Verify your chat ID: Send `/start` to @userinfobot
2. Add chat ID to `.env`: `ADMIN_CHAT_IDS=12345,67890`
3. Restart bot

### Domain switch not working

1. Check domain exists: `ls config/domains/`
2. Check domain config validity:
   ```bash
   python -c "from utils.domain_loader import get_domain_loader; loader = get_domain_loader(); print(loader.list_available_domains())"
   ```

### Learning not working

1. Check database: `ls -lh data/conversations.db`
2. Check learner:
   ```bash
   python -c "from utils.simple_learner import get_learner; learner = get_learner(); print(learner.get_stats())"
   ```

## Monitoring

### Logs

Structured JSON logs are written to stdout:
```bash
python run_bot.py --mode polling 2>&1 | jq .
```

### Metrics

Prometheus metrics available at `/metrics` endpoint (webhook mode):
```bash
curl http://localhost:8000/metrics
```

### Health Check

```bash
curl http://localhost:8000/healthz
curl http://localhost:8000/readyz
```

## Production Deployment

1. **Set environment variables**
2. **Configure webhook**:
   ```bash
   curl -X POST https://api.telegram.org/bot<TOKEN>/setWebhook \
     -d url=https://yourdomain.com/telegram/SECRET \
     -d allowed_updates='["message"]'
   ```
3. **Start webhook server**:
   ```bash
   python run_bot.py --mode webhook
   ```
4. **Setup systemd service** (optional)
5. **Configure nginx reverse proxy** (optional)

## Support

For issues or questions:
1. Check logs
2. Review this guide
3. Check `AI_SELLER_SELF_MODE_GUIDE.md` for detailed architecture
4. Contact admin

---

© SoVAni 2025
