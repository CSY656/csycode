# CsyCode

A command-line AI coding assistant — Phase 1: Interactive Chat.

## Requirements

- Python 3.11+
- Windows (macOS and Linux support coming in future iterations)

## Installation

```bash
# Clone the repository
git clone <repo-url>
cd CsyCode

# Create and activate a virtual environment (recommended)
python -m venv .venv
.venv\Scripts\activate   # Windows

# Install CsyCode in editable mode
pip install -e .
```

## Configuration

Create the configuration file at `%USERPROFILE%\.csycode\config.yaml`
(i.e. `C:\Users\<your-username>\.csycode\config.yaml`):

```yaml
providers:
  - name: my-claude
    protocol: anthropic
    model: claude-sonnet-4-6
    base_url: https://api.anthropic.com
    api_key: sk-ant-...
    thinking: true    # optional, enables extended thinking

  - name: my-openai
    protocol: openai
    model: gpt-4o
    base_url: https://api.openai.com
    api_key: sk-...
```

### Configuration Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Identifier for this provider (used with `-p`) |
| `protocol` | Yes | API protocol: `anthropic` or `openai` |
| `model` | Yes | Model ID (e.g. `claude-sonnet-4-6`, `gpt-4o`) |
| `base_url` | Yes | API base URL |
| `api_key` | Yes | Authentication key |
| `thinking` | No | Enable extended thinking (Anthropic only), defaults to `false` |

## Usage

```bash
# Use the first provider in config
csycode

# Use a specific provider by name
csycode -p my-openai
csycode --provider my-claude

# Show help
csycode --help
```

### In-App Commands

| Command | Action |
|---------|--------|
| `/quit` | Exit the application |
| `/clear` | Clear the current conversation history |

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+T` | Toggle extended thinking panel (when thinking is enabled) |
| `Ctrl+C` | Exit the application |

## Features

- **Streaming SSE responses** — text appears character by character
- **Multi-turn conversation** — AI remembers the full session context
- **Extended thinking** — Claude's thinking process shown in a collapsible panel
- **Markdown rendering** — code blocks, bold, italic, and more
- **Multi-provider** — switch between Anthropic and OpenAI via config

## Project Structure

```
CsyCode/
├── pyproject.toml
├── README.md
├── docs/ch01/               # Design documents
│   ├── spec.md
│   ├── plan.md
│   ├── task.md
│   └── checklist.md
├── src/csycode/
│   ├── main.py              # CLI entry point
│   ├── config.py            # YAML config loading & validation
│   ├── provider.py          # BaseProvider, Message, StreamDelta
│   ├── registry.py          # Provider registry & factory
│   ├── session.py           # Conversation session management
│   ├── providers/
│   │   ├── anthropic.py     # Anthropic Messages API provider
│   │   └── openai.py        # OpenAI Chat Completions provider
│   └── tui/
│       ├── app.py           # Main Textual application
│       ├── app.css          # TUI styles
│       ├── chat_view.py     # Chat message display
│       ├── input_bar.py     # User input with commands
│       └── thinking.py      # Collapsible thinking panel
└── tests/
    ├── test_config.py       # Config tests (9 cases)
    └── test_session.py      # Session tests (6 cases)
```

## Running Tests

```bash
pytest tests/ -v
```
