# 🚀 GitHub Monitor with AI Summarization

[🇷🇺 Русский](README_RU.md) | [🇺🇸 English](README.md)

**GitHub Monitor** is a tool for automatically tracking new releases in selected GitHub repositories.
When a new version is released, the bot downloads the release notes, generates a concise and understandable summary using AI (via OpenRouter), and sends a notification to Telegram.

## 💻 Operating System

This application is designed and tested for **Linux** environments.

## ✨ Features

*   **🔔 Instant Notifications:** Sends messages to Telegram immediately upon detecting a new release.
*   **🧠 AI Analysis:** Turns dry technical changelogs into structured reports (New Features, Fixes, Important).
*   **🔄 Universal Model Support:** Works via OpenRouter API, allowing you to use GPT-4o, Claude 3.5 Sonnet, Google Gemini, and hundreds of other models.
*   **🛡️ Resilience:**
    *   Automatic retries for network or API failures.
    *   Fallback mode: sends the original text if AI is unavailable.
    *   Log rotation (protection against disk overflow).
*   **📝 Smart Formatting:** Correctly handles MarkdownV2 in Telegram and splits long messages into parts.

## 🛠️ Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/RichardMoor75/GitHubMonitor.git
    cd GitHubMonitor
    ```

2.  **Create a virtual environment (recommended):**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## ⚙️ Configuration

### 1. Environment Variables (.env)
Create a `.env` file based on the example:
```bash
cp .env.example .env
```
Open `.env` and fill it in:
*   `MONITOR_BOT_TOKEN`: Your Telegram bot token (from @BotFather).
*   `MONITOR_ADMIN_CHAT_ID`: Your Telegram ID (can be found via @userinfobot).
*   `OPENROUTER_API_KEY`: API key from OpenRouter.
*   `OPENROUTER_MODEL`: Model selection (e.g., `anthropic/claude-haiku-4.5` or `openai/gpt-4o-mini`).
*   `SUMMARY_LANGUAGE`: The language for AI summaries (e.g., "English", "German"). Defaults to "Russian" if not set.
*   `GITHUB_TOKEN`: (Optional but important) Your GitHub Personal Access Token to increase API limits.

### 2. Repository List
Create a `repos_to_monitor.json` file based on the example:
```bash
cp repos_to_monitor.json.example repos_to_monitor.json
```
Edit `repos_to_monitor.json`. Format: `"Display Name": "owner/repo"`.

Example:
```json
{
  "Obsidian": "obsidianmd/obsidian-releases",
  "Python": "python/cpython",
  "VS Code": "microsoft/vscode"
}
```

## 🚀 Usage

### Manual Run
```bash
python3 github_monitor.py
```

### Automatic Run (Cron)
For regular checks (e.g., every 30 minutes), add an entry to crontab.

1.  Open cron editor:
    ```bash
    crontab -e
    ```
2.  Add the line (adjust paths to yours!):
    ```bash
    */30 * * * * /opt/GitHubMonitor/run_monitor.sh
    ```
    *Ensure `run_monitor.sh` is executable (`chmod +x run_monitor.sh`) and contains the correct paths to your venv.*

## 📂 Project Structure

*   `github_monitor.py` — Main script.
*   `.env` — Configuration and secrets.
*   `repos_to_monitor.json` — Database of monitored repositories.
*   `github_releases_state.json` — State file (stores the ID of the last seen release to avoid spam).
*   `github_monitor.log` — Operation log (with rotation: max 5 files of 5 MB).
*   `run_monitor.sh` — Wrapper script for running via Cron.

## ❓ Troubleshooting

**Error: "Rate limit exceeded"**
*   Make sure you added `GITHUB_TOKEN` to `.env`. Without a token, GitHub allows only 60 requests per hour from a single IP.

**Error: "Bad Request: can't parse entities"**
*   Usually indicates a problem with Markdown formatting. The script tries to escape special characters, but if the release has very non-standard markup, a failure may occur. In this case, the bot will send a simplified version of the message.

**Bot is silent, although a release is out**
*   Check `github_monitor.log`.
*   Perhaps this release is already recorded in `github_releases_state.json`. Delete the corresponding line from the JSON file so the bot "forgets" the release and sends it again.
