# cyber-whaleyeah

## Usage

1. Copy `config.sample.json` to `etc/config.json` and edit it.
2. Build and start the services with the host user's UID and GID:

   ```bash
   MYUID="$(id -u)" MYGID="$(id -g)" docker compose up -d --build
   ```

3. Follow the application logs:

   ```bash
   ./control.sh logs
   ```

Use `control.sh` to control the bot after the initial deployment:

| Command | Action |
| --- | --- |
| `./control.sh` or `./control.sh restart` | Restart the existing bot container and follow its logs. |
| `./control.sh build` | Rebuild and apply the bot image, then follow its logs. |
| `./control.sh build --no-logs` | Rebuild and apply the bot image, then return. |
| `./control.sh logs` | Follow the last 100 lines of bot logs. |
| `./control.sh status` | Show all service containers, including stopped ones. |
| `./control.sh --help` | Show command help. |

`--no-logs` also works with `restart`. Restart and build target only the bot;
MongoDB must already be running and healthy before build. The script works from
any working directory and defaults `MYUID`/`MYGID` to the current account's IDs,
while honoring exported overrides. For example,
`MYUID=1000 MYGID=1000 ./control.sh build` uses those IDs for the rebuilt bot.
Press Ctrl+C to leave the log view while the bot continues running.

Python dependencies are locked in `uv.lock` and installed into the application image at build time. Container restarts do not download or reinstall dependencies. Source-only rebuilds reuse the cached dependency layer.

The bot uses OpenAI SDK 3.50 and Gemini SDK 3.3 with HTTPX2 for SDK requests.
Telegram and the existing HTTP helpers continue to use HTTPX 0.28. Gemini is
pinned to a reviewed release because the plugin reconstructs Interactions API streams.
`tool.uv.constraint-dependencies` bounds selected transitive dependencies to the
tested major or pre-1.0 minor series. Compatibility tests cover Gemini tool
history, OpenAI Chat Completions and Responses, Markdown formatting, Telegram
webhooks and scheduling, streaming JSON, and Motor client initialization.

Use the same host account and UID/GID settings for subsequent `docker compose up`
commands, including rebuilds. Both services run as `${MYUID:-1000}:${MYGID:-1000}`;
omitting these variables uses `1000:1000`, which may lose access to the
bind-mounted configuration files and MongoDB data. The configured user needs
read access to `etc/config.json` and `etc/mongod.conf`, and read/write access to
the existing `database` directory and its data files.

When calling Compose directly, to retain these settings across shell sessions,
set numeric `MYUID` and `MYGID`
values in the project's `.env` file, using the deployment account's `id -u` and
`id -g` output. Compose reads `.env` automatically; shell environment values
take precedence.

MongoDB is reachable only through the Compose network at `whaleyeah-mongodb:27017`; its port is not published to the host.

## Gemini

The Gemini plugin uses the Interactions API with `google-genai==3.3.0` and
defaults to `gemini-3.5-flash-lite`. Set `model` in the plugin configuration to
override it. The default model accepts text, images, audio, and PDF documents
and produces text; image generation requires a model that supports image output.

The client explicitly uses `v1beta` to preserve existing model aliases and API
behavior across the SDK 3 upgrade. Code-execution results may contain text or
multimodal content; both forms are retained in conversation memory, with text
and the last inline image delivered to Telegram.

Add a Gemini entry to `plugins` in your configuration, using your API key and
the same administrator whitelist mechanism as the other AI plugins:

```json
"gemini": {
    "api_key": "YOUR_GEMINI_API_KEY",
    "model": "gemini-3.5-flash-lite",
    "generation_config": {"thinking_level": "high"},
    "memory_size": 10,
    "max_attachment_size": "10MiB",
    "whitelist_chat": [-1001145141919810],
    "tools": ["google_search", "url_context", "code_execution"]
}
```

`generation_config` is passed to every Interactions API request, including
follow-up turns. Omit it to use the model defaults. Supported `thinking_level`
values depend on the model; `gemini-3.5-flash-lite` accepts `minimal`, `low`,
`medium`, and `high`. Tool names may optionally use a `tool_` prefix, so
`tool_code_execution` and `code_execution` enable the same tool.

Each request sends its full conversation history with `store=false`. Conversation
memory stays in the bot process. A new command starts a separate conversation;
replying to a cached bot message continues that message's conversation. Replies
to the same message create independent branches. If the message is no longer
cached, only the replied-to message and the current input are sent.

`memory_size` limits cached bot-message entries (not the number of turns in each
conversation), with FIFO eviction and a default of 10. Set it to 0 to disable
memory. Restarting the bot clears the cache. Complete model steps, tool results,
citations, and thought signatures are retained locally for subsequent turns;
Telegram formatting and error messages are kept out of the API history.

Run the migration tests with `uv run --frozen python -m unittest discover -s tests`.
After changing source code or dependencies, with MongoDB already running and
healthy, run `./control.sh build` to rebuild and apply the application image.
Use `./control.sh restart` for a process restart with the existing image.
