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
