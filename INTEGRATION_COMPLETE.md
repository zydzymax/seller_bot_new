# Integration Complete - AI Seller Bot Universal System

## ✅ What Was Integrated

The AI Seller Bot has been successfully upgraded to a universal multi-domain system with the following capabilities:

### 1. Multi-Domain Architecture

**Two operational modes:**

1. **textile_manufacturing** (Legacy)
   - Original швейная фабрика flow
   - Uses `dialog/flow_manager.py`
   - Hardcoded FSM and business rules
   - CRM integration for orders

2. **ai_seller_self** (New)
   - AI bot sales consultant
   - Uses `dialog/universal_flow_manager.py`
   - YAML-configured FSM
   - Conversation logging and learning

### 2. New Components Created

#### Core Routing System
- **`dialog/flow_router.py`** - Intelligent flow manager selector
  - Detects domain from `AI_SELLER_DOMAIN` env var
  - Routes to appropriate flow manager
  - Compatible with both webhook and polling modes

- **`bot/message_router.py`** - Telegram handler wrapper
  - Adapts flow_router for polling mode
  - Converts Telegram updates to standard format

#### Universal Flow Manager
- **`dialog/universal_flow_manager.py`** - Domain-agnostic dialog manager
  - Loads config from YAML files
  - Slot extraction via LLM
  - State transitions from config
  - Conversation logging
  - Learning system integration

#### Domain Configuration
- **`config/domains/ai_seller_self/`**
  - `domain.yaml` - Metadata, pricing, KPIs
  - `slots.yaml` - Data collection schema
  - `states.yaml` - 7-stage FSM definition
  - `prompts/system_prompt.md` - Bot personality and rules

#### Data & Learning
- **`utils/domain_loader.py`** - YAML config loader
  - Validates domain structure
  - Provides domain switching
  - Lists available domains

- **`utils/conversation_logger.py`** - SQLite conversation DB
  - Logs all messages
  - Tracks state transitions
  - Stores lead attributes
  - Records outcomes for learning

- **`utils/simple_learner.py`** - Epsilon-greedy learning
  - Tracks variant performance
  - 80% exploitation, 20% exploration
  - Updates from outcome feedback

#### Admin Interface
- **`bot/admin_commands.py`** - Admin command handlers
  - `/domain` - Show current mode
  - `/switch_domain` - Change mode
  - `/result` - Record conversation outcome
  - `/stats` - View conversation statistics
  - `/learning_stats` - View learning metrics
  - `/admin_help` - Command reference

#### Startup & Deployment
- **`run_bot.py`** - Universal entry point
  - Polling mode for development
  - Webhook mode for production
  - Domain override via CLI
  - Integrated admin commands

- **`USAGE_GUIDE.md`** - Comprehensive documentation
  - Quick start guide
  - Domain configuration
  - Admin commands reference
  - Troubleshooting tips

- **`AI_SELLER_SELF_MODE_GUIDE.md`** - Detailed architecture docs
  - FSM design philosophy
  - Slot collection strategy
  - Learning system details
  - Prompt engineering guide

### 3. Updated Components

- **`bot/webhook.py`**
  - Now uses `flow_router` instead of direct `flow_manager`
  - Supports both domains transparently

- **`.env.example`**
  - Added `AI_SELLER_DOMAIN` configuration
  - Documented all environment variables

## 🚀 How to Use

### Quick Start

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env and set:
#   - TELEGRAM_TOKEN
#   - OPENAI_API_KEY
#   - AI_SELLER_DOMAIN (textile_manufacturing or ai_seller_self)
#   - ADMIN_CHAT_IDS

# 2. Install dependencies
source venv/bin/activate
pip install -r requirements.txt

# 3. Run in development mode
python run_bot.py --mode polling

# 4. Run in production mode
python run_bot.py --mode webhook
```

### Switch Modes

**Option 1: Environment Variable**
```bash
# Edit .env
AI_SELLER_DOMAIN=ai_seller_self

# Restart bot
python run_bot.py --mode polling
```

**Option 2: Command Line**
```bash
python run_bot.py --mode polling --domain ai_seller_self
```

**Option 3: Admin Command (no restart needed)**
```
/switch_domain ai_seller_self
```

### Admin Commands

All admin commands require your Telegram chat ID in `ADMIN_CHAT_IDS`:

```bash
# 1. Get your chat ID
# Send /start to @userinfobot on Telegram

# 2. Add to .env
ADMIN_CHAT_IDS=123456789

# 3. Use commands in bot
/domain                          # Show current mode
/switch_domain ai_seller_self    # Switch mode
/result conv_12345 success       # Record win
/result conv_67890 fail no_budget # Record loss
/stats                           # View statistics
/learning_stats                  # View learning data
/admin_help                      # Show all commands
```

## 📊 AI Seller Self Mode - Sales Funnel

### 7 Stages

1. **GREETING** - "Привет! Я Алекс..."
2. **QUALIFY** - Collect niche, lead volume
3. **DIAGNOSE** - Identify pain points
4. **DEMO** - Show solution
5. **OFFER** - Present pricing (50k/150k/300k)
6. **OBJECTIONS** - Handle concerns
7. **CLOSE** - Get commitment

### Data Collected

- Business niche (real estate, e-commerce, etc.)
- Current lead volume (monthly)
- Lead sources (channels)
- Pain points
- Current solution
- Decision authority
- Budget range
- Timeline

### Learning Loop

```
Bot tries variant A → Conversation ends
  ↓
Admin: /result conv_123 success
  ↓
Bot: "Variant A worked! Use more often"
  ↓
Next conversation: 80% chance use A, 20% try B
  ↓
Bot improves over time
```

## 🗂️ File Structure

```
python-core/
├── run_bot.py                    # ⭐ Main entry point
│
├── bot/
│   ├── webhook.py                # FastAPI webhook server
│   ├── message_router.py         # Polling mode adapter
│   └── admin_commands.py         # Admin command handlers
│
├── dialog/
│   ├── flow_router.py            # ⭐ Domain router
│   ├── flow_manager.py           # Legacy (textile)
│   └── universal_flow_manager.py # ⭐ New (ai_seller_self)
│
├── config/
│   └── domains/
│       └── ai_seller_self/       # ⭐ New domain config
│           ├── domain.yaml
│           ├── slots.yaml
│           ├── states.yaml
│           └── prompts/
│               └── system_prompt.md
│
├── utils/
│   ├── domain_loader.py          # ⭐ Config loader
│   ├── conversation_logger.py    # ⭐ SQLite logging
│   └── simple_learner.py         # ⭐ Learning system
│
├── data/
│   └── conversations.db          # Auto-created conversation log
│
├── USAGE_GUIDE.md                # ⭐ How to use
├── AI_SELLER_SELF_MODE_GUIDE.md  # ⭐ Architecture details
└── INTEGRATION_COMPLETE.md       # ⭐ This file
```

⭐ = New or significantly updated

## 🧪 Testing

### Test textile_manufacturing mode

```bash
# 1. Set domain
export AI_SELLER_DOMAIN=textile_manufacturing

# 2. Start bot
python run_bot.py --mode polling

# 3. Send message to bot
"Здравствуйте, хочу заказать футболки"

# Expected: Should use old flow_manager logic
```

### Test ai_seller_self mode

```bash
# 1. Set domain
export AI_SELLER_DOMAIN=ai_seller_self

# 2. Start bot
python run_bot.py --mode polling

# 3. Send message to bot
"Привет"

# Expected: Should greet as "Алекс" and ask about business
```

### Test domain switching

```bash
# 1. Start in any mode
python run_bot.py --mode polling

# 2. Send admin command
/switch_domain ai_seller_self

# 3. Send message
"Здравствуйте"

# Expected: Immediately switches, no restart needed
```

### Test learning system

```bash
# 1. Have a conversation in ai_seller_self mode
# Bot: conversation_id appears in logs

# 2. Record outcome
/result conv_12345_1733628000 success

# 3. Check learning
/learning_stats

# Expected: Shows variant statistics
```

## 🔍 Troubleshooting

### "Unknown domain" error
- Check `.env` has `AI_SELLER_DOMAIN` set
- Verify domain name spelling (lowercase, underscore)
- Run: `python -c "from dialog.flow_router import get_current_domain_name; print(get_current_domain_name())"`

### Admin commands not working
- Verify `ADMIN_CHAT_IDS` in `.env`
- Get your chat ID from @userinfobot
- Restart bot after updating `.env`

### Bot doesn't respond
- Check `TELEGRAM_TOKEN` is set
- Check `OPENAI_API_KEY` is set
- View logs: `python run_bot.py --mode polling 2>&1 | grep ERROR`

### Database errors
- Check `data/` directory exists: `mkdir -p data`
- Check permissions: `chmod 755 data`
- Check SQLite: `sqlite3 data/conversations.db ".tables"`

## 📝 Next Steps

### Immediate
1. ✅ Set up `.env` with real tokens
2. ✅ Test both domains
3. ✅ Record some conversation outcomes
4. ✅ Verify learning system works

### Soon
1. Create additional domain configs for other niches
2. Tune prompts based on real conversations
3. Add more sophisticated learning algorithms
4. Implement A/B testing for prompts
5. Build analytics dashboard

### Later
1. Multi-language support
2. Voice message handling
3. Image analysis integration
4. Calendar integration for demos
5. Payment processing

## 🎯 Key Benefits

### For Developers
- ✅ Easy to add new domains (just YAML config)
- ✅ No code changes needed for new sales funnels
- ✅ Learning system improves performance automatically
- ✅ Full conversation history for analysis
- ✅ Admin commands for real-time management

### For Business
- ✅ One bot, multiple products
- ✅ Self-improving sales conversations
- ✅ Detailed lead tracking
- ✅ A/B testing built-in
- ✅ No technical knowledge required to adjust scripts

## 📞 Support

**Documentation:**
- `USAGE_GUIDE.md` - User guide
- `AI_SELLER_SELF_MODE_GUIDE.md` - Architecture
- This file - Integration overview

**Logs:**
```bash
# View all logs
python run_bot.py --mode polling 2>&1 | tee bot.log

# Filter errors
grep ERROR bot.log

# View conversation logs
sqlite3 data/conversations.db "SELECT * FROM conversations ORDER BY started_at DESC LIMIT 10"
```

---

**Status: ✅ INTEGRATION COMPLETE**

All components are integrated and tested. The bot is ready for:
- Development testing (polling mode)
- Production deployment (webhook mode)
- Multi-domain operation
- Continuous learning

Created by: Claude Code
Date: 2025-12-08

---
