# cyber-whaleyeah

## Usage

1. Copy `config.sample.json` to `etc/config.json` and edit it.
2. Build and start the services with the host user's UID and GID:

   ```bash
   MYUID="$(id -u)" MYGID="$(id -g)" docker compose up -d --build
   ```

3. Follow the application logs:

   ```bash
   docker compose logs -f whaleyeah
   ```

Python dependencies are locked in `uv.lock` and installed into the application image at build time. Container restarts do not download or reinstall dependencies. Source-only rebuilds reuse the cached dependency layer.

MongoDB is reachable only through the Compose network at `whaleyeah-mongodb:27017`; its port is not published to the host.

## Gemini

The Gemini plugin uses the Interactions API with `google-genai==2.30.1` and
defaults to `gemini-3.5-flash-lite`. Set `model` in the plugin configuration to
override it. The default model accepts text, images, audio, and PDF documents
and produces text; image generation requires a model that supports image output.

Add a Gemini entry to `plugins` in your configuration, using your API key and
the same administrator whitelist mechanism as the other AI plugins:

```json
"gemini": {
    "api_key": "YOUR_GEMINI_API_KEY",
    "model": "gemini-3.5-flash-lite",
    "memory_size": 10,
    "max_attachment_size": "10MiB",
    "whitelist_chat": [-1001145141919810],
    "tools": ["google_search", "url_context", "code_execution"]
}
```

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
After changing dependencies, rebuild the application image with
`docker compose up -d --build whaleyeah` so it installs the updated lock file.
