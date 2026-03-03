# RoboTerri ClawBio Bot

A Telegram bot that runs ClawBio bioinformatics skills using Claude as the reasoning engine. Send genetic data, medication photos, or natural language questions -- get personalised genomic reports back.

## Features

- Claude-powered conversational AI with the RoboTerri persona
- Runs all ClawBio skills: pharmgx, equity, nutrigx, metagenomics, compare, drugphoto
- Handles text messages, genetic file uploads (.txt, .csv, .vcf, .fastq), and medication photos
- All genetic data stays local -- nothing leaves your machine
- Reports and figures sent directly in Telegram

## Prerequisites

- Python 3.11+
- A Telegram account
- An API key from any Anthropic-compatible provider (Anthropic, OpenRouter, AWS Bedrock, etc.)
- ClawBio cloned and working (`python3 clawbio.py run pharmgx --demo`)

## Setup

### 1. Install dependencies

```bash
pip3 install -r bot/requirements.txt
```

### 2. Create a Telegram bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`, choose a name and username
3. Save the **bot token** BotFather gives you

### 3. Get your chat ID

1. Start a conversation with your new bot
2. Send any message
3. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
4. Find `"chat":{"id":123456789}` -- that number is your chat ID

### 4. Configure environment

Create a `.env` file in the ClawBio root directory:

```
TELEGRAM_BOT_TOKEN=your-bot-token-here
TELEGRAM_CHAT_ID=your-chat-id-here
LLM_API_KEY=your-api-key-here
```

The `LLM_API_KEY` works with any Anthropic-compatible provider. For alternative providers, also set the base URL:

```
# OpenRouter
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=sk-or-...

# AWS Bedrock (via proxy)
LLM_BASE_URL=https://your-bedrock-proxy.example.com

# Direct Anthropic (default, no LLM_BASE_URL needed)
LLM_API_KEY=sk-ant-...
```

Optional: set the model (defaults to `claude-sonnet-4-5-20250929`):

```
CLAWBIO_MODEL=claude-sonnet-4-5-20250929
```

`ANTHROPIC_API_KEY` is also accepted as a fallback if `LLM_API_KEY` is not set.

### 5. Run

```bash
python3 bot/roboterri.py
```

## Commands

| Command | Description |
|---|---|
| `/start` | Show welcome message and available commands |
| `/skills` | List all available ClawBio skills |
| `/demo <skill>` | Run a skill with demo data (e.g. `/demo pharmgx`) |

## Usage

- **Text**: Ask any bioinformatics question -- Claude routes to the right skill
- **File upload**: Send a 23andMe .txt, AncestryDNA .csv, or VCF file for analysis
- **Photo**: Send a photo of medication packaging for personalised drug guidance
- **Demo**: Type `/demo pharmgx` to see a pharmacogenomics report with synthetic data

## Security

- The bot only responds to your configured `TELEGRAM_CHAT_ID`
- All genetic data is processed locally
- Never commit your `.env` file (already in `.gitignore`)

---

*ClawBio is a research and educational tool. It is not a medical device and does not provide clinical diagnoses. Consult a healthcare professional before making any medical decisions.*
